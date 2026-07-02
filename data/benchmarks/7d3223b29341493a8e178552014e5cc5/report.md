# JPEG Context Benchmark Report

## Configuration

- Model: `google/gemma-4-26b-a4b-it:free`
- Lengths: [8, 16]
- Seeds: [1103, 2207]
- Rendering: 750×1000 grayscale JPEG, 16 px text, quality 75

## Results

```json
{
  "observations": 28,
  "profiles": {
    "primary": {
      "observations": 28,
      "arms": {
        "jpeg": {
          "field_accuracy": 57.14,
          "probe_accuracy": 57.14,
          "trajectory_success": 0.0,
          "degradation_per_transition": 1.5625,
          "median_latency_ms": 2322,
          "input_tokens": 4694,
          "output_tokens": 183,
          "payload_bytes": 635716,
          "cost": 0.0,
          "failures": 4,
          "resolved_models": [
            "google/gemma-4-26b-a4b-it-20260403:free"
          ],
          "ci95": [
            41.67,
            70.83
          ]
        },
        "text": {
          "field_accuracy": 71.43,
          "probe_accuracy": 71.43,
          "trajectory_success": 25.0,
          "degradation_per_transition": 1.0417,
          "median_latency_ms": 3788,
          "input_tokens": 2568,
          "output_tokens": 172,
          "payload_bytes": 11957,
          "cost": 0.0,
          "failures": 8,
          "resolved_models": [
            "google/gemma-4-26b-a4b-it-20260403:free"
          ],
          "ci95": [
            54.17,
            91.67
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
