# The server accepted 2 messages. Only 1 recipient showed a read.

We ran both [registered conditions](protocol.md) in **Olam's original two-seat Mindustry task**, not a mock server or a new game adapter. Both stock Harbor trials completed. The journal accepted the sender's fresh challenge in both trials. Only the read-enabled participant recorded the challenge and sent a reply derived from its contents. The original journal accepted that reply, and the sender observed it. Counting `say` events alone would incorrectly mark the other arm as observed communication.

| Native outcome | Read enabled | Reads stopped |
| --- | --- | --- |
| Challenge accepted by the game | Yes | Yes |
| Recipient explicitly recorded the challenge | Yes | No |
| Game accepted the challenge-dependent reply | Yes | No |
| Sender observed that reply | Yes | No |
| Usable native survival reward | Unavailable | Unavailable |
| Native trial UUID | `5d4b0628-5b9f-4867-b69d-2ee07dbeab01` | `875967f0-4867-4603-8708-fbff027b4b17` |

The two experimental agents used readiness, status and team chat only. They stopped when the protocol finished. Neither trial measured survival or the effect of communication on survival. We withhold both native rewards rather than record a zero. The no-read arm does **not** establish that the server hid the message or that the message never appeared in an incidental response. The positive arm establishes a specific content-dependent exchange by these programs, not language understanding or an effect on gameplay.

## Reproduce or inspect

- [Original Olam task](https://github.com/olamlabs/multi-agent-mindustry/tree/1374e46a229c73763a1e46f347f67676ff1b434b/mindustry-endless-survival) and [original Olam Harbor](https://github.com/olamlabs/harbor-multiagent/tree/4a194016336ccd1ce3fcc799b10158ca0375eb63).
- [Completed Linux run](https://github.com/PeterMakhnatch/harbor-seatlab/actions/runs/44541369056). One standard 4-CPU runner executed the 2 arms sequentially. We used the registered CPU override, no model and no retries. Both Harbor processes exited 0. The game map, clock, verifier and world code stayed unchanged.
- [Durable public measurements](results.json). They include the 2 native IDs, role receipt availability, protocol verdicts, process exit statuses and reward disposition. They contain no raw game journal, challenge, authentication token, host path, replay, or private log.
- [Workflow](../../.github/workflows/native-mindustry-chat.yml) and [registered read rules](protocol.md). Each new run uses a new random challenge. The observer tests the same evidence rules; trial IDs and challenge contents do not repeat.

For a task author: decide which event the benchmark needs. A server-accepted post shows transmission. A role observation bound to the challenge shows an explicit read. An accepted reply bound to that challenge shows dependent use by the receiving program. These are different results. Keep each one separate from game score.
