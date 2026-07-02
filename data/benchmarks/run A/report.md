# JPEG Context Benchmark Report

## Configuration

- Model: `openai/gpt-5-nano`
- Lengths: [8, 16]
- Seeds: [1103, 2207]
- Rendering: 750×1000 grayscale JPEG, 16 px text, quality 75

## Results

```json
{
  "observations": 44,
  "profiles": {
    "closed_loop": {
      "observations": 16,
      "arms": {
        "jpeg": {
          "field_accuracy": 37.5,
          "probe_accuracy": 37.5,
          "trajectory_success": 0.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 1266,
          "input_tokens": 11873,
          "output_tokens": 138,
          "payload_bytes": 784656,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            25.0,
            50.0
          ]
        },
        "text": {
          "field_accuracy": 87.5,
          "probe_accuracy": 87.5,
          "trajectory_success": 50.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 802,
          "input_tokens": 3202,
          "output_tokens": 123,
          "payload_bytes": 12709,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            75.0,
            100.0
          ]
        }
      },
      "comparable_model": true,
      "tradeoff": {
        "input_tokens_saved": -8671,
        "input_token_savings_percent": -270.8,
        "accuracy_delta_points": -50.0,
        "latency_delta_ms": 464,
        "payload_bytes_delta_percent": 6074.02,
        "cost_delta": 0.0
      }
    },
    "primary": {
      "observations": 28,
      "arms": {
        "jpeg": {
          "field_accuracy": 71.43,
          "probe_accuracy": 71.43,
          "trajectory_success": 0.0,
          "degradation_per_transition": 1.0417,
          "median_latency_ms": 1254,
          "input_tokens": 16748,
          "output_tokens": 240,
          "payload_bytes": 668492,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            66.67,
            75.0
          ]
        },
        "text": {
          "field_accuracy": 92.86,
          "probe_accuracy": 92.86,
          "trajectory_success": 75.0,
          "degradation_per_transition": -1.5625,
          "median_latency_ms": 968,
          "input_tokens": 3073,
          "output_tokens": 225,
          "payload_bytes": 12008,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            81.25,
            100.0
          ]
        }
      },
      "comparable_model": true,
      "tradeoff": {
        "input_tokens_saved": -13675,
        "input_token_savings_percent": -445.0,
        "accuracy_delta_points": -21.43,
        "latency_delta_ms": 286,
        "payload_bytes_delta_percent": 5467.06,
        "cost_delta": 0.0
      }
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
