# pi-goal-x-py

Goal-driven planning extension for [pi-python](https://github.com/zy1233/pi-python).

Python port of [pi-goal-x](https://www.npmjs.com/package/pi-goal-x).

## Install

```bash
pip install pi-goal-x-py
```

## Usage

Use `/goal <description>` to enter goal-driven mode. The agent will plan
steps and track progress using `goal_update` and `goal_complete` tools.

## Behaviour worth knowing

- **Saved when it changes.** `/goal`, `goal_update` and `goal_complete` each save the goal in the
  session (a custom `goal_state` entry), so a resumed session picks up the last state. A goal that
  was completed is not brought back.
- **Steps are numbered from 0.** The status lists them as `[0]`, `[1]`, ... and `goal_update`
  takes that number as `step_index`. A number that names no step, or a new step without a
  description, is an error the model sees; nothing changes.
- **Reminders are limited.** When every step is done and the agent has not called
  `goal_complete`, it is reminded, at most twice (`pi_goal_x.MAX_COMPLETION_REMINDERS`). The count
  starts again when a step is reopened or a new goal begins.

Details: [Phase 7 spec, section 6](../../docs/specs/2026-09-22-phase7-extension-api-design.md).
