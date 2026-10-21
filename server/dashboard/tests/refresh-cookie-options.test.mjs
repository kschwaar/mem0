import assert from "node:assert/strict";
import test from "node:test";
import { shouldUseSecureCookie } from "../src/lib/refresh-cookie-options.mjs";

test("loopback HTTP can receive the refresh cookie without Secure", () => {
  assert.equal(
    shouldUseSecureCookie("http://localhost:3000", "production"),
    false,
  );
  assert.equal(
    shouldUseSecureCookie("http://127.0.0.1:3000", "production"),
    false,
  );
  assert.equal(shouldUseSecureCookie("http://[::1]:3000", "production"), false);
});

test("HTTPS and non-loopback HTTP retain Secure refresh cookies", () => {
  assert.equal(
    shouldUseSecureCookie("https://dashboard.example", "production"),
    true,
  );
  assert.equal(
    shouldUseSecureCookie("http://dashboard.example", "production"),
    true,
  );
});

test("missing or invalid dashboard URLs preserve the environment default", () => {
  assert.equal(shouldUseSecureCookie(undefined, "production"), true);
  assert.equal(shouldUseSecureCookie("not a URL", "production"), true);
  assert.equal(shouldUseSecureCookie(undefined, "development"), false);
});
