# JPEG Context Benchmark Report

## Configuration

- Model: `openai/gpt-5-nano-2025-08-07`
- Lengths: [16]
- Seeds: [1103]
- Rendering: 750×1000 grayscale JPEG, 16 px text, quality 75, image detail `low`

## Results

```json
{
  "observations": 64,
  "profiles": {
    "closed_loop": {
      "observations": 8,
      "arms": {
        "jpeg": {
          "field_accuracy": 25.0,
          "probe_accuracy": 25.0,
          "trajectory_success": 0.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 1389,
          "input_tokens": 6262,
          "output_tokens": 68,
          "payload_bytes": 395442,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            25.0,
            25.0
          ]
        },
        "text": {
          "field_accuracy": 100.0,
          "probe_accuracy": 100.0,
          "trajectory_success": 100.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 946,
          "input_tokens": 1925,
          "output_tokens": 62,
          "payload_bytes": 8114,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            100.0,
            100.0
          ]
        }
      },
      "comparable_model": true,
      "tradeoff": {
        "input_tokens_saved": -4337,
        "input_token_savings_percent": -225.3,
        "accuracy_delta_points": -75.0,
        "latency_delta_ms": 443,
        "payload_bytes_delta_percent": 4773.58,
        "cost_delta": 0.0
      }
    },
    "density_sweep": {
      "observations": 48,
      "arms": {
        "jpeg": {
          "field_accuracy": 41.67,
          "probe_accuracy": 41.67,
          "trajectory_success": 0.0,
          "degradation_per_transition": -0.0311,
          "median_latency_ms": 1667,
          "input_tokens": 58305,
          "output_tokens": 403,
          "payload_bytes": 6099306,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            16.67,
            62.5
          ]
        },
        "text": {
          "field_accuracy": 58.33,
          "probe_accuracy": 58.33,
          "trajectory_success": 0.0,
          "degradation_per_transition": -0.1022,
          "median_latency_ms": 880,
          "input_tokens": 39455,
          "output_tokens": 410,
          "payload_bytes": 164976,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            45.83,
            70.83
          ]
        }
      },
      "comparable_model": true,
      "tradeoff": {
        "input_tokens_saved": -18850,
        "input_token_savings_percent": -47.78,
        "accuracy_delta_points": -16.66,
        "latency_delta_ms": 787,
        "payload_bytes_delta_percent": 3597.09,
        "cost_delta": 0.0
      }
    },
    "primary": {
      "observations": 8,
      "arms": {
        "jpeg": {
          "field_accuracy": 75.0,
          "probe_accuracy": 75.0,
          "trajectory_success": 0.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 1600,
          "input_tokens": 5110,
          "output_tokens": 68,
          "payload_bytes": 215921,
          "cost": 0.0,
          "failures": 0,
          "resolved_models": [
            "openai/gpt-5-nano-2025-08-07"
          ],
          "ci95": [
            75.0,
            75.0
          ]
        },
        "text": {
          "field_accuracy": 75.0,
          "probe_accuracy": 75.0,
          "trajectory_success": 0.0,
          "degradation_per_transition": 0.0,
          "median_latency_ms": 1018,
          "input_tokens": 1289,
          "output_tokens": 66,
          "payload_bytes": 5529,
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
        "input_tokens_saved": -3821,
        "input_token_savings_percent": -296.43,
        "accuracy_delta_points": 0.0,
        "latency_delta_ms": 582,
        "payload_bytes_delta_percent": 3805.25,
        "cost_delta": 0.0
      }
    }
  },
  "token_crossover": {
    "closed_loop": {
      "first_jpeg_token_win_length": null,
      "points": [
        {
          "trajectory_length": 32,
          "jpeg_input_tokens": 1565.5,
          "text_input_tokens": 481.25,
          "input_tokens_saved_by_jpeg": -1084.25
        }
      ]
    },
    "density_sweep": {
      "first_jpeg_token_win_length": null,
      "points": [
        {
          "trajectory_length": 16,
          "jpeg_input_tokens": 1277.5,
          "text_input_tokens": 322.0,
          "input_tokens_saved_by_jpeg": -955.5
        },
        {
          "trajectory_length": 32,
          "jpeg_input_tokens": 1277.5,
          "text_input_tokens": 446.25,
          "input_tokens_saved_by_jpeg": -831.25
        },
        {
          "trajectory_length": 64,
          "jpeg_input_tokens": 1276.75,
          "text_input_tokens": 715.75,
          "input_tokens_saved_by_jpeg": -561.0
        },
        {
          "trajectory_length": 128,
          "jpeg_input_tokens": 1854.25,
          "text_input_tokens": 1274.25,
          "input_tokens_saved_by_jpeg": -580.0
        },
        {
          "trajectory_length": 256,
          "jpeg_input_tokens": 3005.5,
          "text_input_tokens": 2410.0,
          "input_tokens_saved_by_jpeg": -595.5
        },
        {
          "trajectory_length": 512,
          "jpeg_input_tokens": 5884.75,
          "text_input_tokens": 4695.5,
          "input_tokens_saved_by_jpeg": -1189.25
        }
      ]
    },
    "primary": {
      "first_jpeg_token_win_length": null,
      "points": [
        {
          "trajectory_length": 16,
          "jpeg_input_tokens": 1277.5,
          "text_input_tokens": 322.25,
          "input_tokens_saved_by_jpeg": -955.25
        }
      ]
    }
  },
  "charts": [
    "charts/accuracy-by-length.png",
    "charts/accuracy-by-probe.png",
    "charts/survival-by-depth.png",
    "charts/efficiency.png",
    "charts/capacity-and-cost.png",
    "charts/token-crossover-by-density.png"
  ]
}
```

## Interpretation

This pilot estimates paired effects and variance. It does not establish model-independent superiority of either representation.
