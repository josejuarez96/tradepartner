# Anthropic API vendor terms for research labeling

Read 2026-10-11. This records the terms check required by ADR 0013 point 6 for the Claude comparison arm in research-labeling amendment #1504. It authorizes no call by itself; the arm still needs the owner's key and all three positive spend ceilings.

| Question | Finding |
| --- | --- |
| Training on inputs | The [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) section B say Anthropic may not train models on customer content. The [commercial data-training notice](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training) says commercial inputs and outputs are not used for training by default. Explicitly supplied feedback or opt-in materials are exceptions. No opt-out is needed for the default API posture. |
| Output ownership | The [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) section B say the customer retains input rights and owns outputs; Anthropic disclaims rights and assigns any output rights it receives. |
| Retention and deletion | The [commercial retention notice](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data) says API inputs and outputs are deleted from the backend within 30 days, except for a different agreement, usage-policy enforcement or law. [Zero data retention](https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to) is an approved enterprise arrangement, not the default. The [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) section E.4 provide for destruction of confidential information on request, subject to legal and backup exceptions. |
| Personal non-commercial research | Anthropic's [API individual-use answer](https://support.claude.com/en/articles/8987200-can-i-use-the-claude-api-for-individual-use) welcomes individuals and hobbyists, with API use under the Commercial Terms. The [Commercial Terms](https://www.anthropic.com/legal/commercial-terms) say the API is not a consumer offering; they do not ban non-commercial research. The arm compares labels over public filings; it does not resell the service or train a competing model. |

The ADR 0013 training bar is met on these terms. The owner still controls the actual key, ceiling and first real call.
