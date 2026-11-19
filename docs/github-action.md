# Verify a Harbor study in another repository

A task maintainer can test a study on each pull request without rerunning the world. The SeatLab action checks a trusted manifest pin, recomputes every registered analysis, and produces JSON and offline HTML. It uses the same implementation as `seatlab study verify`. A green check means **the analysis reproduces**, not that a participant or intervention succeeds.

## Copy a working example

The [consumer on the personal Harbor fork](https://github.com/PeterMakhnatch/harbor-multiagent/tree/example/seatlab-study-action/examples/seatlab-verify) checks a real 2-trial job. Copy its `recorded-job/` directory to `studies/recorded-job/` to use this exact example. It contains 1 manifest and 3 small input files. GitHub downloads the pinned action and your selected files. The verification step itself runs offline and requires no model, package install, database or Docker service.

```yaml
name: Verify Harbor study
on: [pull_request]
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6.1.0
        with:
          persist-credentials: false
          sparse-checkout: studies
      - id: evidence
        uses: PeterMakhnatch/harbor-seatlab@e23c962ceca8aaa5c07c1824a1f5cf79b99d51fa
        with:
          manifest: studies/recorded-job/study.json
          expected-sha256: f289c8c89a12bcb8c25753a8b3f5a5ac389825baceba874921e6e2587556110d
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: verified-harbor-study
          path: |
            ${{ steps.evidence.outputs.report }}
            ${{ steps.evidence.outputs.html }}
```

The upload step is optional. Open `report.html` from its artifact offline. For a custom study, use the `study_sha256` that [`seatlab study seal`](portable-studies.md) prints. It is the canonical manifest digest, not the SHA-256 of the indented JSON file.

The [completed run in the Harbor fork](https://github.com/PeterMakhnatch/harbor-multiagent/actions/runs/44543633325) verified the recorded job and refused the different pin. It also checked that the failed step had no outputs.

| Interface | Meaning |
| --- | --- |
| Input `manifest` | Relative POSIX file path under `GITHUB_WORKSPACE`. Absolute paths, symlinks and `..` are refused. |
| Input `expected-sha256` | A required, trusted 64-character lowercase SHA-256 digest. |
| Output `report` | The verified JSON summary in a new directory under `RUNNER_TEMP`. |
| Output `html` | The self-contained report in that directory. |
| Output `checks` | Number of recomputed registered analyses. This is not a game score. |

The action uses Python 3.11 or later. It starts Python with `-I -S`, imports the verifier from its own checkout, and runs no programs from the study. It does not print participant inputs, actions, check IDs or file paths on failure. A wrong pin or changed input produces a failed action and no report outputs. The consumer example also runs this negative control on unchanged input data.

## What the digest does not prove

Anyone who controls both the study and the pin can replace both. Keep the pin and action SHA under separate trusted review from unreviewed study updates. Protect the workflow or require a review by its owner. Treat PR changes to the workflow as code changes. No write permission or secrets are needed. A matching digest is not proof of authorship or independent attestation.
