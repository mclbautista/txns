# 8. Name collisions in variant texts are dropped, not failed

Status: proposed (issues #29 and #31; awaiting Cyril's acceptance). Amends the failure handling in ADR 0007.

The model is never shown a ledger name: the payload is scrubbed and every request is leak-checked. A match between a model's variant text and a blocked name is therefore a coincidence, usually a plain word the books also use as a name (a ledger description with no " - " is read as a vendor name, so "Delivery" blocks every text with that word). Failing the whole draft on such a match made `author` exit 4 on repeated coincidences.

Decision: in a variants answer, a text that matches a blocked name is dropped locally and never sent back or saved; the rest of the answer is checked as before. An item left under its counts is re-asked inside the one existing re-ask, by item location only, with the answer minus the dropped texts and a request for spare variants. The allowlist, the request leak check, name detection, gate 3 and promotion checks are unchanged, and a name anywhere else in a draft (other parts, keys, seller or vendor fields) still fails it.

Trade-off: a name that did reach the model by some other route would now be dropped with a warning instead of stopping the run. The dropped text never leaves the machine either way. Making the name index stop treating ordinary single-word descriptions as names is a separate, larger change to detection and is not made here.

Follow-up (issue #31): a real run lost 11 texts in the first answer and 8 in the re-ask, and two items stayed under their counts, so a fixed spare of two was not enough. Two changes, both inside the one re-ask and neither relaxing a check: the surplus asked of an item is two for each text it lost (at least two, never past the 12-variant cap) and only for the kind of variant that lost texts; and when the re-ask answer still leaves an item short, texts of the first answer that survived collision-dropping fill the gap (`parts.with_survivors`), then the whole merged answer is validated as any other. An item short even then still fails the draft with exit 4.
