# Drift Scan Review Packets

The review-packet renderer turns drift scan JSON into a concise markdown
handoff artifact for human or AI-assisted review.

Generate scan JSON first:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json --output-format json > scan.json
```

Render a packet from a file:

```sh
python3 -m enforcement.review_packet --input scan.json
```

Or pipe JSON through stdin:

```sh
python3 -m enforcement.cli --config examples/drift-scan.json --output-format json \
  | python3 -m enforcement.review_packet
```

## Real Local Pipeline Example

A local handoff run can scan the notes and playbook repositories, pass JSON
through stdin, and render the markdown packet in one command:

```sh
python3 -m enforcement.cli --notes-root ../cross-repo-threads --playbook-root ../ai-workflow-playbook/docs --ignore 'archive/**' --output-format json | python3 -m enforcement.review_packet
```

One observed run scanned 38 notes files and 20 playbook files, ignored 471
paths, and reported 1 candidate. Human review classified that remaining
candidate as acceptable duplication / false positive noise. The evidence was a
repeated heading only, token similarity 0.2038, an existing canonical reference,
and repeated headings of `model` and `promotion criteria`. No cleanup was
needed for the accepted noise.

The packet includes:

- scan summary
- candidate count
- grouped candidate evidence
- suggested reviewer questions
- an explicit reminder that classification remains human-reviewed

Use the packet as a local handoff aid. The JSON input is advisory signal
transport, and the markdown output does not infer final classifications, create
work items, clean up content, persist decisions, or enforce policy.
