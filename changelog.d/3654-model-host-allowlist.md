- #3654: research.labeling.api_base_url and research.claude.api_base_url are refused at settings load unless they name their vendor's host over https (no userinfo, no other port).
### Changed
- The two model base URLs (`research.labeling.api_base_url`, `research.claude.api_base_url`) must name their vendor's host: another host, a look-alike suffix host, userinfo or a non-443 port is refused at settings load, naming the setting and never the value, so a wrong env line cannot send a model key elsewhere (#3654).
