// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

// Keep this crate as the thin static-library root for the product-owned Rust
// closure. Runtime dependencies belong in gkrust-naivefox.
#[cfg(feature = "naivefox")]
extern crate gkrust_naivefox;
