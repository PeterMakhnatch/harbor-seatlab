# When does a game message become evidence of a read?

A server can accept a team message even when the receiving policy never reads it. This experiment separates three events: the original game accepts a challenge, the receiving participant reports reading that challenge, and the game accepts a reply that depends on its contents. None of these is a survival score.

## Two fixed attempts

Use the [original Olam survival task](https://github.com/olamlabs/multi-agent-mindustry/tree/1374e46a229c73763a1e46f347f67676ff1b434b/mindustry-endless-survival) without modifying its world, seats, verifier, map, game settings or code. Use [the Harbor revision its author names](https://github.com/olamlabs/harbor-multiagent/tree/4a194016336ccd1ce3fcc799b10158ca0375eb63). Run 2 trials on an ordinary public Ubuntu runner. Use no model. If fewer than 8 worker CPUs are available, request only the available CPUs through Harbor. The resulting timings do not measure game performance.

Both participants register and wait for the game to run. `seat-2` sends a fixed readiness message. Only after `seat-1` reads that message does it generate a fresh 128-bit challenge inside its own seat. It sends the challenge to the team. The receiving process has not been given the challenge through its program arguments or a shared file.

| Attempt | What the receiving program does after arming | What would establish a read |
| --- | --- | --- |
| `read` | Reads the game API, checks the sender, hashes the challenge, and sends the digest in a team reply | The accepted challenge, the recipient's matching read receipt, and the game's accepted reply from the other seat |
| `no-read` | Makes no further API read after arming, waits 8 seconds and exits | A server record by itself is insufficient. Expect no recipient read receipt or challenge-dependent reply. |

Both actor programs use only readiness, status, and team messaging. They submit no game-control actions. The game clock continues independently. The programs finish once the communication protocol ends; they do not wait for a game loss. A living core at that point is not a survival score of zero.

## Evidence limits

The official journal records accepted message posts, not `GET /update` reads. The `seq` inside a recipient's private view is not the server journal sequence. Bind the events by sender and challenge digest, not by wall time or a shared sequence number. The no-read condition tests explicit program observation. It does not establish that the world hid the message, that the API never included it in an incidental response, or that a model understood anything.

Retain both registered attempts, including infrastructure failures. Do not replace them. Keep the trial UUID, availability of evidence, protocol results and native score disposition separate. Do not publish raw journal contents, native replay files, challenge text, tokens or host logs. Ship no game image or JAR. Configure the experiment to stop within 46 minutes. The main game, tutorial and licenses remain with their authors.
