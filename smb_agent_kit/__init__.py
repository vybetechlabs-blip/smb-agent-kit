"""smb-agent-kit: shared parts for approval-gated agents.

Modules: `approval` (queue and state machine), `delivery` (senders and the send log), `llm`
(providers with per-call records), `evals` (saved runs and report helpers), `config` (YAML
loading with the fictional-data check), and `cli` (the approval verbs as a command group).
"""

__version__ = "0.1.0"
