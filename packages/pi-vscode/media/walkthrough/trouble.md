# If something does not work

- **"No model is set" or "needs setup".** Run the setup (step 2).
- **"The model provider did not accept the API key".** Run the setup again and replace the key.
- **"The provider does not know this model".** The model id or the base URL is not right. Open `agent.toml`, correct the `[model]` table (`id`, `base_url`), and select **Restart** in the chat.
- **"Could not reach the model server".** Check the base URL in `agent.toml` and your network.
- **The agent does not start.** Select **Show log** to read what the agent said.

An example with all keys: `agent.example.toml` in the repository (https://github.com/zy1233/pi-python/blob/main/packages/pi-agent-cli/agent.example.toml).
