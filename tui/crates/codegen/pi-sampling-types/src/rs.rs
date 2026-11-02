//! The Responses API item types that session history still stores.
//!
//! These are the subset of `async-openai`'s `types::responses` (MIT licence,
//! © 2022 Himanshu Neema) that `ConversationItem::Reasoning` and
//! `BackendToolKind` embed. The serde shapes are the same, so old session
//! files and `raw_output` entries keep loading.

use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum OutputStatus {
    InProgress,
    Completed,
    Incomplete,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct ReasoningTextContent {
    pub text: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct ReasoningItem {
    pub id: String,
    pub summary: Vec<SummaryPart>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content: Option<Vec<ReasoningTextContent>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub encrypted_content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<OutputStatus>,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SummaryPart {
    SummaryText(SummaryTextContent),
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct SummaryTextContent {
    pub text: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum WebSearchToolCallStatus {
    InProgress,
    Searching,
    Completed,
    Failed,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct WebSearchActionSearchSource {
    pub r#type: String,
    pub url: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct WebSearchActionSearch {
    pub query: String,
    pub sources: Option<Vec<WebSearchActionSearchSource>>,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct WebSearchActionOpenPage {
    pub url: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct WebSearchActionFind {
    pub url: String,
    pub pattern: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WebSearchToolCallAction {
    Search(WebSearchActionSearch),
    OpenPage(WebSearchActionOpenPage),
    Find(WebSearchActionFind),
    FindInPage(WebSearchActionFind),
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct WebSearchToolCall {
    pub action: WebSearchToolCallAction,
    pub id: String,
    pub status: WebSearchToolCallStatus,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct CustomToolCall {
    pub call_id: String,
    pub input: String,
    pub name: String,
    pub id: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum CodeInterpreterToolCallStatus {
    InProgress,
    Completed,
    Incomplete,
    Interpreting,
    Failed,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct CodeInterpreterToolCall {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    pub container_id: String,
    pub id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outputs: Option<Vec<CodeInterpreterToolCallOutput>>,
    pub status: CodeInterpreterToolCallStatus,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum CodeInterpreterToolCallOutput {
    Logs(CodeInterpreterOutputLogs),
    Image(CodeInterpreterOutputImage),
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct CodeInterpreterOutputLogs {
    pub logs: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct CodeInterpreterOutputImage {
    pub url: String,
}

/// The output items a legacy `raw_output` entry can lift into siblings.
/// Any other item type (`message`, `function_call`, ...) fails to parse and
/// the caller skips it.
#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum OutputItem {
    Reasoning(ReasoningItem),
    WebSearchCall(WebSearchToolCall),
    CustomToolCall(CustomToolCall),
    CodeInterpreterCall(CodeInterpreterToolCall),
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ResponseFormat {
    Text,
    JsonObject,
    JsonSchema { json_schema: ResponseFormatJsonSchema },
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct ResponseFormatJsonSchema {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub schema: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub strict: Option<bool>,
}
