# JPEG Context Benchmark Report

## Configuration

- Model: `google/gemma-4-26b-a4b-it:free`
- Lengths: [4]
- Seeds: [1103]
- Rendering: 750×1000 grayscale JPEG, 16 px text, quality 75

## Results

```json
{
  "observations": 2,
  "profiles": {
    "primary": {
      "observations": 2,
      "arms": {
        "jpeg": {
          "field_accuracy": 0.0,
          "probe_accuracy": 0.0,
          "trajectory_success": 0.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 1464,
          "input_tokens": 334,
          "output_tokens": 12,
          "payload_bytes": 28816,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "google/gemma-4-26b-a4b-it-20260403:free"
          ],
          "ci95": [
            0.0,
            0.0
          ]
        },
        "text": {
          "field_accuracy": 100.0,
          "probe_accuracy": 100.0,
          "trajectory_success": 100.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 2417,
          "input_tokens": 179,
          "output_tokens": 20,
          "payload_bytes": 601,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "google/gemma-4-26b-a4b-it-20260403:free"
          ],
          "ci95": [
            100.0,
            100.0
          ]
        }
      },
      "comparable_model": true
    }
  },
  "charts": [
    "charts/accuracy-by-length.png",
    "charts/accuracy-by-probe.png",
    "charts/survival-by-depth.png",
    "charts/efficiency.png",
    "charts/capacity-and-cost.png"
  ]
}
```

## Interpretation

This pilot estimates paired effects and variance. It does not establish model-independent superiority of either representation.
