# Dify Marketplace scope — AssetFare 1.0.0

AssetFare 1.0.0 is intentionally limited to two strictly read-only evaluation
tools: capabilities and quote.

It calls one fixed, documented HTTPS origin and contains no wallet,
authentication, prepare, session, transaction-construction, signing,
submission, funding, swap, bridge-execution, code/command/SQL execution,
filesystem access, browser automation, arbitrary URL fetch, executable binary,
or credential.

The package sends no personal financial data. A quote is an estimate and
authorizes nothing. This narrow scope is the complete Marketplace capability
set; no executable action is hidden behind either tool.

Submission classification requested: **Low risk, read-only Tool plugin**.

References:

- <https://github.com/langgenius/dify-plugins/blob/main/docs/plugin-submission-requirements.md>
- <https://github.com/langgenius/dify-plugins/blob/main/docs/plugin-review-guidelines.md>
- <https://docs.dify.ai/en/develop-plugin/publishing/standards/privacy-protection-guidelines>
