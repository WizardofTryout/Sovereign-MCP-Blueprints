# =============================================================================
# Sovereign MCP Blueprints — Compliance Policies
# PII Redaction Enforcement Policy (OPA)
#
# This Open Policy Agent (OPA) file explicitly denies any Claude tool-call
# if the 'pii_redaction_level' is set to 'none' or is missing when required.
# 
# Purpose: Programmatic enforcement of GDPR Article 25 (Data Protection by Design)
# =============================================================================

package sovereign.mcp.compliance

import future.keywords.in
import future.keywords.contains
import future.keywords.if

# Default stance is to deny access unless explicitly allowed
default allow := false

# We allow the tool execution ONLY if redaction is active and valid
allow if {
    is_redaction_active
}

# Redaction is considered disabled if explicitly set to "none"
pii_redaction_disabled if {
    input.context.pii_redaction_level == "none"
}

# Redaction is active if it's explicitly set to something else (e.g., "high", "standard")
is_redaction_active if {
    input.context.pii_redaction_level != "none"
    # Ensure the field actually exists in the request context
    has_key(input.context, "pii_redaction_level")
}

# Generate a clear compliance violation message when denied
deny contains msg if {
    pii_redaction_disabled
    msg := "GDPR VIOLATION PREVENTED: Tool execution blocked. 'pii_redaction_level' is set to 'none'. Mandatory PII redaction cannot be disabled in this environment."
}

deny contains msg if {
    not has_key(input.context, "pii_redaction_level")
    msg := "COMPLIANCE ERROR: Tool execution blocked. 'pii_redaction_level' context parameter is missing. All requests must explicitly define their redaction posture."
}

# Helper function to check if an object has a specific key
has_key(obj, k) if {
    _ = obj[k]
}
