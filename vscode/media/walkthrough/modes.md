# Choose how much zycode asks

The mode menu is in the header of the chat. A new chat always starts in **Ask**, or in **Auto** if you set that in the settings. It never starts in **Always approve**.

- **Ask**: zycode asks before each action that is not read-only.
- **Auto**: zycode runs the actions that are clearly in scope, and asks about the rest. Auto is not a sandbox. Auto can send your messages and some `bash` commands to a model.
- **Always approve**: zycode asks about nothing, `bash` included. A warning shows before you turn it on. It stays on for the open chat only, and it is not offered in a folder that you did not trust.

The setting `zycode.permissionMode` sets the mode of a start (Ask or Auto).
