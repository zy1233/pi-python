use std::any::TypeId;
use std::fmt;

use tokio::sync::{mpsc, oneshot};

use crate::{
    common::{AcpChannelFailure, AcpResult, acp_channel_failure_error, acp_internal_error},
    message::{AcpAgentMessage, AcpArgs, AcpClientMessage, AcpMethod, AcpRequest},
};

/// Vendor grok-build extensions. pi-python speaks standard ACP only.
fn vendor_ext_method(rpc_method: &str, request: &impl serde::Serialize) -> Option<String> {
    if rpc_method != "ext_method" && rpc_method != "ext_notification" {
        return None;
    }
    let value = serde_json::to_value(request).ok()?;
    let method = value.get("method")?.as_str()?;
    if method.starts_with("x.ai/") || method.starts_with("_x.ai/") {
        Some(method.to_owned())
    } else {
        None
    }
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
    if let Some(vendor) = vendor_ext_method(method, &request) {
        tracing::debug!(method = %vendor, "dropping x.ai/* ACP extension (standard ACP only)");
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
    use super::acp_send;
    use crate::common::{AcpChannelFailure, acp_channel_failure};
    use crate::message::AcpAgentMessage;
    use agent_client_protocol as acp;
    use tokio::sync::mpsc;

    fn ext_request() -> acp::ExtRequest {
        acp::ExtRequest::new(
            "custom/test",
            serde_json::value::to_raw_value(&serde_json::json!({}))
                .unwrap()
                .into(),
        )
    }

    fn vendor_ext_request() -> acp::ExtRequest {
        acp::ExtRequest::new(
            "x.ai/session/list",
            serde_json::value::to_raw_value(&serde_json::json!({}))
                .unwrap()
                .into(),
        )
    }

    #[tokio::test]
    async fn vendor_x_ai_ext_is_dropped_without_sending() {
        let (tx, mut rx) = mpsc::unbounded_channel::<AcpAgentMessage>();
        let result = acp_send(vendor_ext_request(), &tx).await;
        assert!(result.is_ok(), "vendor ext should dummy-succeed: {result:?}");
        assert!(
            rx.try_recv().is_err(),
            "x.ai/* must never be enqueued toward the agent"
        );
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
