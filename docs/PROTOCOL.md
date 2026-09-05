# Local WebSocket protocol v1

Connect to `ws://127.0.0.1:8766/ws` from the same-origin UI. One controller is allowed. HTTP `/api/health` reports local providers and simulated hardware. Use localhost only; remote authentication/TLS are not implemented.

Every conversation message carries a monotonically increasing client `request_id` integer. Discard received conversation events with other IDs. Robot events are independent of conversation IDs. This is an initial protocol, not a versioned public hardware API.

| Client message `type` | Other fields |
| --- | --- |
| `user_text` | `request_id`, `text` ≤ 2000 characters |
| `audio_turn` | `request_id`, `wav`: base64 mono 16-bit PCM 16 kHz WAV, ≤30 seconds; UI caps turns at 15 seconds |
| `interrupt` | `request_id` for the next generation; first cancel playback locally |
| `playback_started`, `playback_finished` | `request_id`, `segment_id` matching one sequentially played audio segment |
| `action` | `action`: `offer_banana`, `take_banana`, `reload`, `stop_motion`, or simulator-only `reset_simulation` |
| `expression` | `expression`: `curious`, `happy`, `sleepy` |

Server events: `hello` (protocol/providers), `robot_state` (authoritative state/events), `action_result` (acceptance/reason), `turn_started`, `interrupted`, `voice_state`, `transcript`, `assistant_delta`, `audio` (segment_id/text/base64 WAV), `metric` (name/value in milliseconds), `tool_result`, `turn_done`, `error`.

`turn_done` means generation finished, not that the recipient heard every segment. Send finished acknowledgements only after actual playback. `action_result.accepted` means a behavior was accepted/started, not a grasp or delivery succeeded. `robot_state.banana` changes only after adapter acknowledgement; current adapter is simulated. Closing the socket cancels its voice session and requests a motion stop.
