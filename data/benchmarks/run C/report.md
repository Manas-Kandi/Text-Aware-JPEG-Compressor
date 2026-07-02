# JPEG Context Benchmark Report

## Configuration

- Model: `openai/gpt-5-nano`
- Lengths: [8, 16]
- Seeds: [1103, 2207]
- Rendering: 750×1000 grayscale JPEG, 16 px text, quality 75, image detail `low`

## Results

```json
{
  "observations": 44,
  "profiles": {
    "closed_loop": {
      "observations": 16,
      "arms": {
        "jpeg": {
          "field_accuracy": 50.0,
          "probe_accuracy": 50.0,
          "trajectory_success": 0.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 1360,
          "input_tokens": 12521,
          "output_tokens": 131,
          "payload_bytes": 787640,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            50.0,
            50.0
          ]
        },
        "text": {
          "field_accuracy": 75.0,
          "probe_accuracy": 75.0,
          "trajectory_success": 0.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 865,
          "input_tokens": 3853,
          "output_tokens": 124,
          "payload_bytes": 16192,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            75.0,
            75.0
          ]
        }
      },
      "comparable_model": true,
      "tradeoff": {
        "input_tokens_saved": -8668,
        "input_token_savings_percent": -224.97,
        "accuracy_delta_points": -25.0,
        "latency_delta_ms": 495,
        "payload_bytes_delta_percent": 4764.38,
        "cost_delta": 0.0
      }
    },
    "primary": {
      "observations": 28,
      "arms": {
        "jpeg": {
          "field_accuracy": 78.57,
          "probe_accuracy": 78.57,
          "trajectory_success": 25.0,
          "degradation_per_transition": -1.0417,
          "median_latency_ms": 1305,
          "input_tokens": 17882,
          "output_tokens": 245,
          "payload_bytes": 674582,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            68.75,
            92.76
          ]
        },
        "text": {
          "field_accuracy": 85.71,
          "probe_accuracy": 85.71,
          "trajectory_success": 50.0,
          "degradation_per_transition": -3.125,
          "median_latency_ms": 892,
          "input_tokens": 4207,
          "output_tokens": 238,
          "payload_bytes": 18098,
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
        "input_tokens_saved": -13675,
        "input_token_savings_percent": -325.05,
        "accuracy_delta_points": -7.14,
        "latency_delta_ms": 413,
        "payload_bytes_delta_percent": 3627.38,
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
