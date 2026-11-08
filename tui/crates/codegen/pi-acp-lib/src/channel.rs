use std::any::TypeId;
use std::fmt;

use tokio::sync::{mpsc, oneshot};

use crate::{
    common::{AcpChannelFailure, AcpResult, acp_channel_failure_error, acp_internal_error},
    message::{AcpAgentMessage, AcpArgs, AcpClientMessage, AcpMethod, AcpRequest},
};

/// The name of the vendor extension that `request` carries, if it carries one.
///
/// The vendor extensions belong to the old Rust runtime. pi-python speaks standard ACP and its
/// own `pi/*` extensions only, so it sends nothing under the vendor prefix. The prefix comes
/// with and without the `_` that the wire adds to an extension name.
///
/// The name comes from the typed value ([`AcpMethod::ext_name`]). It cannot come from the
/// serialized form: `ExtRequest` and `ExtNotification` serialize their params only, and skip
/// the method name.
fn vendor_ext_method(request: &impl AcpMethod) -> Option<&str> {
    let method = request.ext_name()?;
    (method.starts_with("x.ai/") || method.starts_with("_x.ai/")).then_some(method)
}

fn dummy_vendor_response<R: 'static>() -> AcpResult<R> {
    let boxed: Box<dyn std::any::Any> = if TypeId::of::<R>() == TypeId::of::<()>() {
        Box::new(())
    } else {
        let ext: agent_client_protocol::ExtResponse =
            serde_json::from_value(serde_json::json!({}))
                .or_else(|_| serde_json::from_value(serde_json::Value::Null))
                .map_err(|e| acp_internal_error(format!("dropped vendor extension: {e}")))?;
        Box::new(ext)
    };
    boxed
        .downcast::<R>()
        .map(|value| *value)
        .map_err(|_| acp_internal_error("dropped vendor extension: unexpected response type"))
}

/// Receiver/sender pair, either for client/agent or agent/client message types.
pub struct AcpChannel<I, O> {
    pub rx: mpsc::UnboundedReceiver<I>,
    pub tx: mpsc::UnboundedSender<O>,
}

impl<I: AcpMethod, O: AcpMethod> AcpChannel<I, O> {
    pub fn new(rx: mpsc::UnboundedReceiver<I>, tx: mpsc::UnboundedSender<O>) -> Self {
        Self { rx, tx }
    }
}

/// Client channel: receive client messages from agent, send agent messages to agent.
pub type AcpClientChannel = AcpChannel<AcpClientMessage, AcpAgentMessage>;
/// Agent channel: receive agent messages from client, send client messages to client.
pub type AcpAgentChannel = AcpChannel<AcpAgentMessage, AcpClientMessage>;

/// Create a linked pair of client/agent channels.
pub fn acp_channels() -> (AcpClientChannel, AcpAgentChannel) {
    let (tx1, rx1) = mpsc::unbounded_channel();
    let (tx2, rx2) = mpsc::unbounded_channel();
    (AcpChannel::new(rx1, tx2), AcpChannel::new(rx2, tx1))
}

pub async fn acp_send<R, T>(request: T, tx: &mpsc::UnboundedSender<R>) -> AcpResult<T::Response>
where
    T: AcpRequest,
    R: From<AcpArgs<T>> + fmt::Debug,
{
    let method = request.method_name();
    if let Some(vendor) = vendor_ext_method(&request) {
        tracing::debug!(method = %vendor, "dropping a vendor ACP extension (standard ACP only)");
        return dummy_vendor_response();
    }

    let (response_tx, response_rx) = oneshot::channel();
    let args = AcpArgs {
        request,
        response_tx,
    };

    tx.send(args.into()).map_err(|_| {
        acp_channel_failure_error(
            format!("unable to send '{method}' request, channel closed"),
            AcpChannelFailure::SendFailed,
        )
    })?;

    response_rx.await.map_err(|_| {
        acp_channel_failure_error(
            format!("unable to receive '{method}' response, channel closed"),
            AcpChannelFailure::RecvFailed,
        )
    })?
}

#[cfg(test)]
mod acp_send_failure_tests {
    use super::{acp_send, vendor_ext_method};
    use crate::common::{AcpChannelFailure, AcpResult, acp_channel_failure};
    use crate::message::{AcpAgentMessage, AcpArgs, AcpRequest};
    use agent_client_protocol as acp;
    use std::task::Poll;
    use tokio::sync::mpsc;

    fn ext_request() -> acp::ExtRequest {
        acp::ExtRequest::new(
            "custom/test",
            serde_json::value::to_raw_value(&serde_json::json!({}))
                .unwrap()
                .into(),
        )
    }

    /// A vendor name, as the pager wrote it and as the wire shows it (with the `_`).
    const VENDOR_NAMES: [&str; 2] = ["x.ai/log", "_x.ai/log"];

    fn vendor_ext_request() -> acp::ExtRequest {
        acp::ExtRequest::new(
            VENDOR_NAMES[0],
            serde_json::value::to_raw_value(&serde_json::json!({}))
                .unwrap()
                .into(),
        )
    }

    fn ext_notification(method: &str) -> acp::ExtNotification {
        acp::ExtNotification::new(
            method,
            serde_json::value::to_raw_value(&serde_json::json!({}))
                .unwrap()
                .into(),
        )
    }

    /// The answer of `acp_send`, if it has one at once.
    ///
    /// A message that goes to the agent waits for the agent. A test must not `.await` it: if
    /// the drop of the vendor extensions broke, the test would wait for ever and not fail. A
    /// message that is dropped has its answer on the first poll.
    async fn answer_at_once<T>(
        request: T,
        tx: &mpsc::UnboundedSender<AcpAgentMessage>,
    ) -> Option<AcpResult<T::Response>>
    where
        T: AcpRequest,
        AcpAgentMessage: From<AcpArgs<T>>,
    {
        let mut send = Box::pin(acp_send(request, tx));
        match futures::poll!(send.as_mut()) {
            Poll::Ready(answer) => Some(answer),
            Poll::Pending => None,
        }
    }

    #[tokio::test]
    async fn vendor_x_ai_ext_is_dropped_without_sending() {
        let (tx, mut rx) = mpsc::unbounded_channel::<AcpAgentMessage>();
        let result = answer_at_once(vendor_ext_request(), &tx)
            .await
            .expect("a dropped vendor request is answered at once");
        assert!(
            result.is_ok(),
            "vendor ext should dummy-succeed: {result:?}"
        );
        assert!(
            rx.try_recv().is_err(),
            "a vendor extension must never be enqueued toward the agent"
        );
    }

    /// The pager sent its log lines as a vendor notification once a second, and nothing dropped
    /// them: the guard read the method name from the serialized request, and `ExtNotification`
    /// serializes its params only.
    #[tokio::test]
    async fn vendor_x_ai_notification_is_dropped_without_sending() {
        let (tx, mut rx) = mpsc::unbounded_channel::<AcpAgentMessage>();
        for method in VENDOR_NAMES {
            let result = answer_at_once(ext_notification(method), &tx)
                .await
                .unwrap_or_else(|| panic!("{method}: a dropped notification is answered at once"));
            assert!(result.is_ok(), "{method} should dummy-succeed: {result:?}");
        }
        assert!(
            rx.try_recv().is_err(),
            "a vendor extension must never be enqueued toward the agent"
        );
    }

    #[tokio::test]
    async fn other_extensions_are_still_sent() {
        // `pi/*` is ours. The others only contain the vendor prefix, or are close to it.
        let near_misses = ["custom/x.ai/log", "x.ai", "x.aix/log"];
        for method in ["pi/yolo_mode_changed", "pi/session/delete", "custom/test"]
            .into_iter()
            .chain(near_misses)
        {
            let (tx, mut rx) = mpsc::unbounded_channel::<AcpAgentMessage>();
            let mut send_fut = Box::pin(acp_send(ext_notification(method), &tx));
            assert!(
                futures::poll!(send_fut.as_mut()).is_pending(),
                "{method}: the request waits for its answer"
            );
            assert!(
                rx.try_recv().is_ok(),
                "{method} must be enqueued toward the agent"
            );
        }
    }

    #[test]
    fn vendor_ext_method_reads_the_typed_name() {
        for name in VENDOR_NAMES {
            assert_eq!(vendor_ext_method(&ext_notification(name)), Some(name));
        }
        assert_eq!(
            vendor_ext_method(&vendor_ext_request()),
            Some(VENDOR_NAMES[0])
        );
        assert_eq!(vendor_ext_method(&ext_notification("pi/follow_ups")), None);
        // A standard ACP message carries no extension name.
        assert_eq!(vendor_ext_method(&acp::CancelNotification::new("s1")), None);
    }

    #[tokio::test]
    async fn send_failed_when_receiver_dropped_before_send() {
        let (tx, rx) = mpsc::unbounded_channel::<AcpAgentMessage>();
        drop(rx); // no peer listening -> enqueue fails
        let err = acp_send(ext_request(), &tx).await.unwrap_err();
        assert_eq!(
            acp_channel_failure(&err),
            Some(AcpChannelFailure::SendFailed)
        );
    }

    #[tokio::test]
    async fn recv_failed_when_response_channel_dropped_after_send() {
        let (tx, mut rx) = mpsc::unbounded_channel::<AcpAgentMessage>();
        let mut send_fut = Box::pin(acp_send(ext_request(), &tx));
        // First poll enqueues the request, then parks on the response channel.
        assert!(futures::poll!(send_fut.as_mut()).is_pending());
        // The peer "receives" the request then drops it (dropping response_tx).
        drop(rx.try_recv().expect("request should be enqueued"));
        let err = send_fut.await.unwrap_err();
        assert_eq!(
            acp_channel_failure(&err),
            Some(AcpChannelFailure::RecvFailed)
        );
    }
}
