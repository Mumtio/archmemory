# requests — Architecture Notes

Written for engineers joining the project. These are the rules the code cannot
tell you, recorded so they are not lost when people move on.

## Layering

The library is arranged in four layers. Dependencies flow strictly downward.

    api.py          the functional interface: get(), post(), ...
      |
    sessions.py     connection reuse, cookie persistence, redirect handling
      |
    adapters.py     transport; owns the urllib3 connection pools
      |
    models.py       Request / Response data structures
      |
    utils.py        leaf helpers, depended on by everything above

Rules that follow from this:

- adapters.py must not import api.py or sessions.py. The transport layer sits
  below the session layer; importing upward creates a cycle and couples the
  transport to the calling convention above it.
- utils.py must not import api.py, sessions.py, or adapters.py. utils is a leaf
  module that every other module depends on, so any upward import is a cycle.
- models.py must not import api.py or sessions.py. Models describe request and
  response data and must not reach back into the layer that constructs them.

## Protected components

compat.py must not be modified without owner sign-off. It is the single shim
for interpreter and urllib3 differences, and every module imports it. Changes
here have repository-wide blast radius and have historically caused breakage
that only surfaces on one platform.

packages.py must not be refactored. It exists to provide backwards-compatible
import aliases for urllib3 and idna. Downstream code imports through it, and
removing the aliases breaks consumers silently at import time.

## Capability reuse

utils.py already contains a large set of helpers that are easy to miss and
frequently reimplemented by mistake: header parsing, encoding detection, URL
requoting, proxy selection, and CIDR/network membership checks. Search before
adding anything there.
