import path from "node:path";
import { describe, expect, it } from "vitest";

import { agentConfigPath, classifyAuthRequired, classifyTurnFailure, failureNotice, setupText } from "../src/core/first-run.js";

describe("classifyAuthRequired", () => {
  it("knows the text of an agent with no model", () => {
    expect(classifyAuthRequired("No model is set. Choose the setup login.")).toEqual({ reason: "no-model" });
  });
  it("reads the name of the variable that has no value", () => {
    expect(classifyAuthRequired("The variable DEEPSEEK_API_KEY has no value. Run the setup.")).toEqual({ reason: "no-key", variable: "DEEPSEEK_API_KEY" });
  });
  it("does not guess about another text", () => {
    expect(classifyAuthRequired("Authentication required")).toEqual({ reason: "unknown" });
  });
});

describe("setupText", () => {
  it("has a text for each reason, and none of them asks for a key in the chat", () => {
    for (const info of [{ reason: "no-model" as const }, { reason: "no-key" as const, variable: "K" }, { reason: "unknown" as const }, undefined]) {
      expect(setupText(info)).not.toMatch(/paste|type .* here/i);
    }
    expect(setupText({ reason: "no-key", variable: "K" })).toContain("variable K");
  });
});

describe("classifyTurnFailure", () => {
  it("finds a key that the provider refuses", () => {
    expect(classifyTurnFailure("Error: Error code: 401 - {'error': {'message': 'Incorrect API key provided'}}")).toBe("key");
  });
  it("finds a model that does not exist", () => {
    expect(classifyTurnFailure("Error: Error code: 404 - model_not_found, The model `x` does not exist")).toBe("model");
  });
  it("finds a server that cannot be reached", () => {
    expect(classifyTurnFailure("Error: Connection error.")).toBe("connection");
  });
  it("ignores an answer that only talks about keys and errors", () => {
    expect(classifyTurnFailure("Your API key is wrong, you said. A 401 is unauthorized.")).toBeUndefined();
    expect(classifyTurnFailure("Fine. Error: not a thing we know")).toBeUndefined();
  });
  it("reads the last error only", () => {
    expect(classifyTurnFailure("Error: Connection error.\nError: Error code: 401")).toBe("key");
  });
});

describe("failureNotice", () => {
  it("offers the setup for a key and the config file for a model", () => {
    expect(failureNotice("key").actions.map((a) => a.id)).toContain("runSetup");
    expect(failureNotice("model").actions.map((a) => a.id)).toContain("openAgentConfig");
    expect(failureNotice("connection").actions.map((a) => a.id)).toContain("openLog");
  });
});

describe("agentConfigPath", () => {
  it("uses PI_HOME when it is set, and ~ in it", () => {
    expect(agentConfigPath({ PI_HOME: "/x/home" }, "/h")).toBe(path.join("/x/home", "agent.toml"));
    expect(agentConfigPath({ PI_HOME: "~/pi" }, "/h")).toBe(path.join("/h", "pi", "agent.toml"));
  });
  it("uses ~/.pi-python otherwise", () => {
    expect(agentConfigPath({}, "/h")).toBe(path.join("/h", ".pi-python", "agent.toml"));
    expect(agentConfigPath({ PI_HOME: "" }, "/h")).toBe(path.join("/h", ".pi-python", "agent.toml"));
  });
});
