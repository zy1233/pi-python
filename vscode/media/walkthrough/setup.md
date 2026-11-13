# Choose a model and save your key

zycode needs a model and an API key. The setup asks for them in a terminal.

1. Select **Run setup**. A terminal opens.
2. Choose the provider. The bundled agent has the OpenAI-format provider. Many vendors and gateways use that format: give their address when the setup asks for a base URL.
3. Type the model id, as the provider names it.
4. Type the API key. The terminal hides it.

Where the key goes: the key is saved in the agent's own folder, in `local.env` (`~/.pi-python/local.env`, or the folder in `PI_HOME`). Only your user can read that file. zycode does not read, log or store the key. Type the key in the terminal only. Never type it in the chat or in the VS Code settings.

When the terminal closes, zycode restarts the agent.

To use more than one model, or to change the model id or the base URL later, edit `agent.toml` in the same folder. Select **Open agent.toml** in the last step of this guide.

You can run the setup again at any time: Command Palette, **zycode: Set up model and API key**.
