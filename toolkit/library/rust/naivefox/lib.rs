// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at http://mozilla.org/MPL/2.0/.

extern crate abridged_certs;
extern crate app_collator_glue;
extern crate binary_http;
extern crate cert_storage;
extern crate crypto_hash;
extern crate data_storage;
extern crate encoding_glue;
extern crate gecko_logger;
extern crate gecko_tracing;
extern crate gkrust_utils;
extern crate happy_eyeballs_glue;
extern crate http_sfv;
extern crate idna_glue;
extern crate ipcclientcerts;
extern crate ipdl_utils;
extern crate jsrust_shared;
extern crate locale_service_glue;
extern crate fluent_langneg;
extern crate fluent_langneg_ffi;
extern crate mozurl;
extern crate netwerk_helper;
extern crate neqo_glue;
extern crate nserror;
extern crate nsstring;
extern crate oblivious_http;
extern crate pdf_trust_anchors;
extern crate prefs_parser;
extern crate qwac_trust_anchors;
extern crate signature_cache;
extern crate ssl_tokens_cache;
extern crate static_prefs;
extern crate storage;
extern crate trust_anchors;
extern crate unicode_bidi_ffi;
extern crate unic_langid;
extern crate unic_langid_ffi;
extern crate uritemplate_glue;
extern crate urlpattern_glue;
extern crate xpcom;

use std::ffi::CStr;
use std::os::raw::c_char;

use gecko_logger::GeckoLogger;
use log::info;

#[no_mangle]
pub extern "C" fn GkRust_Init() {
    let _ = GeckoLogger::init();
    gecko_tracing::initialize_tracing();
}

#[no_mangle]
pub extern "C" fn GkRust_Shutdown() {}

#[no_mangle]
pub unsafe extern "C" fn intentional_panic(message: *const c_char) {
    panic!("{}", unsafe { CStr::from_ptr(message) }.to_string_lossy());
}

#[no_mangle]
pub unsafe extern "C" fn debug_log(target: *const c_char, message: *const c_char) {
    let target = unsafe { CStr::from_ptr(target) }.to_str().unwrap();
    let message = unsafe { CStr::from_ptr(message) }.to_string_lossy();
    info!(target: target, "{}", message);
}
