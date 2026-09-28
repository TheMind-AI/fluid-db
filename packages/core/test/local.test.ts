import { describe, expect, test } from "bun:test"
import { Conformance } from "../src/conformance"
import { Local } from "../src/local"

Conformance.store({ describe, test, expect }, () => Local.store())
