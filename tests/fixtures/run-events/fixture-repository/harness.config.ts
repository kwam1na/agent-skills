export default {
  "gateId": "run-events-fixture.pr-admission",
  "baseRef": "origin/main",
  "storageNamespace": "delivery-harness/",
  "acceptedEnvelopeSpecs": [
    "delivery-evidence/1"
  ],
  "identityVersions": [
    "delivery-harness-tree/v1"
  ],
  "computingIdentityVersion": "delivery-harness-tree/v1",
  "reviewNeutral": [
    {
      "prefix": "docs/reports/"
    },
    {
      "prefix": "docs/solutions/"
    },
    {
      "prefix": "telemetry/delivery-runs/"
    },
    {
      "prefix": "delivery/records/"
    }
  ],
  "recordNeutral": [
    {
      "prefix": "delivery/records/"
    }
  ],
  "pathClassification": {
    "generated": [
      {
        "kind": "prefix",
        "value": "packages/conformance/vectors/"
      }
    ],
    "test": [
      {
        "kind": "glob",
        "value": "**/*.test.ts"
      }
    ],
    "lockfile": [
      {
        "kind": "glob",
        "value": "**/package-lock.json"
      }
    ]
  },
  "activationThreshold": 1,
  "providers": [
    {
      "id": "delivery-harness.independent-review",
      "findingCodes": []
    }
  ],
  "agentEnvSignals": [
    "CLAUDE_CODE",
    "CLAUDECODE"
  ],
  "ciPolicies": [
    {
      "id": "github-actions",
      "requiredEnv": [
        {
          "variable": "GITHUB_ACTIONS",
          "equals": "true"
        },
        {
          "variable": "CI",
          "equals": "true"
        }
      ]
    }
  ],
  "ciPolicyEnvKey": "DELIVERY_HARNESS_CI_POLICY",
  "preparationWiringPaths": [
    "harness.config.ts"
  ],
  "preparationCommands": [],
  "obligations": [
    {
      "id": "review.green",
      "activation": {
        "kind": "relevant_change"
      },
      "freshness": "exact_candidate",
      "providers": [
        "delivery-harness.independent-review"
      ],
      "acceptedPayloadSpecs": [
        "review.green/2",
        "review.green/1"
      ],
      "allowedResolutionKinds": [
        "satisfied_evidence",
        "waived",
        "not_applicable"
      ],
      "humanWaiverAllowed": true,
      "minimumAttestationLevel": "self",
      "ciDelegationPolicyIds": [],
      "remediation": {
        "default": [
          {
            "id": "run-the-review",
            "summary": "Run the code review and submit its evidence manifest with `delivery-harness submit-evidence`.",
            "kind": "manual_action"
          }
        ]
      },
      "waivableCodes": [
        "review_evidence_missing",
        "stale_evidence",
        "evidence_not_green",
        "unresolved_actionable_findings"
      ],
      "nonWaivableCodes": [
        "ambiguous_records",
        "malformed_record",
        "unknown_provider",
        "live_provider_missing",
        "ambiguous_live_provider",
        "live_provider_failed",
        "resolution_not_allowed"
      ]
    }
  ],
  "deliveryRecordPath": "delivery/records/record.json",
  "deliveryRecordVerification": {
    "baseMovement": "stale"
  },
  "sensitivePaths": [
    {
      "id": "gate-kernel",
      "patterns": [
        {
          "kind": "prefix",
          "value": "packages/kernel/src/"
        }
      ]
    }
  ]
};
