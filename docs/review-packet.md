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

The packet includes:

- scan summary
- candidate count
- grouped candidate evidence
- suggested reviewer questions
- an explicit reminder that classification remains human-reviewed

Use the packet as a local handoff aid. The JSON input is advisory signal
transport, and the markdown output does not infer final classifications, create
work items, clean up content, persist decisions, or enforce policy.
