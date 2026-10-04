- #696: accept_rejections fence tightened: keyword pass only to reviewed callees (_start, ResumeInvocationRow), flag attributes refused, builtins/globals patching refused; control-flow laundering stays a documented limit.
### Changed
- accept_rejections fence (#696): the flag may be passed on only to reviewed callees, any attribute named for it is refused, and patching isinstance/type/bool through builtins, globals or locals is refused.
