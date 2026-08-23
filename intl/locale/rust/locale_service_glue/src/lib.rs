/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at http://mozilla.org/MPL/2.0/. */

use icu_locale::{
    langid,
    subtags::{region, variant, Script},
    LanguageIdentifier, LocaleExpander, ParseError,
};
use nsstring::{nsACString, nsAString};

fn langid_for_bytes(mut name: &[u8]) -> Result<LanguageIdentifier, ParseError> {
    if name.eq_ignore_ascii_case(b"ja-jp-mac") {
        Ok(langid!("ja-JP-macos"))
    } else {
        // Cut out any `.FOO` like `en-US.POSIX`.
        if let Some(ptr) = name.iter().position(|b| b == &b'.') {
            name = &name[..ptr];
        }
        // Locale::ToString may contain extensions.  Likely subtags operate on
        // the language/script/region base name, just like the old ICU path.
        let mut offset = 0;
        while let Some(relative) = name[offset..].iter().position(|b| b == &b'-') {
            let start = offset + relative + 1;
            let end = name[start..]
                .iter()
                .position(|b| b == &b'-')
                .map(|relative| start + relative)
                .unwrap_or(name.len());
            if end - start == 1 {
                name = &name[..offset + relative];
                break;
            }
            offset = end;
            if offset >= name.len() {
                break;
            }
        }
        LanguageIdentifier::try_from_utf8(name)
    }
}

pub fn langid_for_mozilla(name: &nsACString) -> Result<LanguageIdentifier, ParseError> {
    langid_for_bytes(name.as_ref())
}

/// Apply the UTS #35 likely-subtags operation used by the retained C++ Locale
/// API.  This keeps the implementation on the same ICU4X data and expander as
/// the other locale-service helpers instead of reintroducing ICU4C.
#[no_mangle]
pub extern "C" fn locale_service_likely_subtags(
    name: &nsACString,
    add: bool,
    out: &mut nsACString,
) -> bool {
    let mut langid = match langid_for_mozilla(name) {
        Ok(langid) => langid,
        Err(_) => return false,
    };

    let expander = LocaleExpander::new_extended();
    if add {
        expander.maximize(&mut langid);
    } else {
        expander.minimize(&mut langid);
    }

    out.assign(langid.to_string().as_str());
    true
}

/// Raw-pointer variant used by the ICU4C-free C++ Locale component.  Keeping
/// this ABI independent of XPCOM string types lets that small static library
/// reuse the same ICU4X locale data without inheriting libxul-only headers.
#[no_mangle]
pub unsafe extern "C" fn locale_service_likely_subtags_raw(
    name: *const u8,
    name_len: usize,
    add: bool,
    out: *mut u8,
    out_capacity: usize,
) -> isize {
    if name.is_null() || out.is_null() {
        return -1;
    }
    let input = core::slice::from_raw_parts(name, name_len);
    let mut langid = match langid_for_bytes(input) {
        Ok(langid) => langid,
        Err(_) => return -1,
    };
    let expander = LocaleExpander::new_extended();
    if add {
        expander.maximize(&mut langid);
    } else {
        expander.minimize(&mut langid);
    }
    let rendered = langid.to_string();
    if rendered.len() > out_capacity {
        return -2;
    }
    core::ptr::copy_nonoverlapping(rendered.as_ptr(), out, rendered.len());
    rendered.len() as isize
}

/// The unicode ellipsis char "…", or "...", depending on the locale.
#[no_mangle]
pub extern "C" fn locale_service_ellipsis(name: &nsACString, out: &mut nsAString) {
    match langid_for_mozilla(name) {
        Ok(langid) if langid.language.as_str() == "ja" => out.assign_str("..."),
        _ => out.assign_str("…"),
    }
}

/// If true, accesskeys should always be appended for the locale.
#[no_mangle]
pub extern "C" fn locale_service_always_append_accesskeys(name: &nsACString) -> bool {
    match langid_for_mozilla(name) {
        Ok(langid) => match langid.language.as_str() {
            "as" | "ja" => true,
            "zh" if langid.region == Some(region!("TW")) => true,
            _ => false,
        },
        Err(_) => false,
    }
}

/// If false, accesskeys should not be separated from the label.
#[no_mangle]
pub extern "C" fn locale_service_insert_separator_before_accesskeys(name: &nsACString) -> bool {
    match langid_for_mozilla(name) {
        Ok(langid) => match langid.language.as_str() {
            "ja" | "ko" => false,
            "zh" if langid.region == Some(region!("CN")) => false,
            _ => true,
        },
        Err(_) => true,
    }
}

/// A comma-separated list of valid BCP 47 language tags.
///
/// The default value is either
///
/// The shape is `<lang>, en-US, en`
///
/// or
///
/// or `<lang>-<region>, <lang>, en-US, en` when a region is present.
///
/// if the current locale includes a region subtag.
///
/// If customizing this, begin with the language tag of your locale.
/// Next, include language tags for other languages that you expect most users of your locale to be able to speak,
/// so that their browsing experience degrades gracefully if content is not available in their primary language.
///
/// By default, "en-US, en" is appended to the end of the list, providing locales of last resort.
/// If you know that users of your locale would prefer a different variety of English,
/// or if they are not likely to understand English at all,
/// you may opt to include a different English language tag,
/// or to exclude English altogether.
#[no_mangle]
pub extern "C" fn locale_service_default_accept_languages(name: &nsACString, out: &mut nsACString) {
    let mut add_en_us = true;
    let lang;
    let fmt;
    let langs = match langid_for_mozilla(name) {
        Ok(langid) => {
            lang = langid.language;
            match lang.as_str() {
                "ace" => "ace, id",
                "ach" => "ach, en-GB",
                "af" => "af, en-ZA, en-GB",
                "ak" => "ak, ak-GH",
                "an" => "an, es-ES, es, ca",
                "ast" => "ast, es-ES, es",
                "az" => "az-AZ, az",
                "bo" => "bo-CN, bo-IN, bo",
                "br" => "br, fr-FR, fr",
                "brx" => "brx, as",
                "bs" => "bs-BA, bs",
                "ca" if langid.variants.get(0) == Some(&variant!("valencia")) => "ca-valencia, ca",
                "cak" => "cak, kaq, es",
                "crh" => "tr-TR, tr",
                "cs" => "cs, sk",
                "csb" => "csb, csb-PL, pl",
                "cy" => "cy-GB, cy",
                "dsb" => "dsb, hsb, de",
                "el" => "el-GR, el",
                "en" => {
                    add_en_us = false;
                    match langid.region {
                        Some(region) => match region.as_str() {
                            "CA" => "en-CA, en-US, en",
                            "GB" => "en-GB, en",
                            "ZA" => "en-ZA, en-GB, en-US, en",
                            _ => "en-US, en",
                        },
                        _ => "en-US, en",
                    }
                }
                "et" => "et, et-EE",
                "fa" => "fa-IR, fa",
                "ff" => "ff, fr-FR, fr, en-GB",
                "fi" => "fi-FI, fi",
                "fr" => "fr, fr-FR",
                "frp" => "frp, fr-FR, fr",
                "fur" => "fur-IT, fur, it-IT, it",
                "fy" => "fy-NL, fy, nl",
                "ga" => "ga-IE, ga, en-IE, en-GB",
                "gd" => "gd-GB, gd, en-GB",
                "gl" => "gl-ES, gl",
                "gn" => "gn, es",
                "gv" => "gv, en-GB",
                "he" => "he, he-IL",
                "hr" => "hr, hr-HR",
                "hsb" => "hsb, dsb, de",
                "hto" => "es-MX, es-ES, es, es-AR, es-CL",
                "hu" => "hu-HU, hu",
                "hye" => "hye, hy",
                "ilo" => "ilo-PH, ilo",
                "it" => "it-IT, it",
                "ixl" => "ixl, es-MX, es",
                "ja" => "ja", // Also catches ja-JP-mac
                "ka" => "ka-GE, ka",
                "kab" => "kab-DZ, kab, fr-FR, fr",
                "kk" => "kk, ru, ru-RU",
                "kn" => "kn-IN, kn",
                "ko" => "ko-KR, ko",
                "lb" => "lb, de-DE, de",
                "lg" => "lg, en-GB",
                "lij" => "lij, it",
                "lt" => {
                    add_en_us = false;
                    "lt, en-US, en, ru, pl"
                }
                "ltg" => "ltg, lv",
                "mai" => "mai, hi-IN, en",
                "meh" => "meh, es-MX, es",
                "mix" => "mix, es-MX, es",
                "mk" => "mk-MK, mk",
                "ml" => "ml-IN, ml",
                "mr" => "mr-IN, mr",
                "my" => {
                    add_en_us = false;
                    "my, en-GB, en"
                }
                "nb" => "nb-NO, nb, no-NO, no, nn-NO, nn",
                "nn" => "nn-NO, nn, no-NO, no, nb-NO, nb",
                "nr" => "nr-ZA, nr, en-ZA, en-GB",
                "nso" => "nso-ZA, nso, en-ZA, en-GB",
                "oc" => "oc, ca, fr, es, it",
                "pa" => "pa, pa-IN",
                "ppl" => "ppl, es-MX, es",
                "rm" => "rm, rm-CH, de-CH, de",
                "ro" => {
                    add_en_us = false;
                    "ro-RO, ro-GB, en"
                }
                "ru" => "ru-RU, ru",
                "sah" => "sah, ru-RU, ru",
                "sc" => "sc, it-IT, it",
                "scn" => "scn, it-IT, it",
                "sco" => {
                    add_en_us = false;
                    "sco, en-GB, en"
                }
                "si" => "si-LK, si",
                "sk" => "sk, cs",
                "sl" => {
                    add_en_us = false;
                    "sl, en-GB, en"
                }
                "son" => "son, son-ML, fr",
                "sq" => "sq, sq-AL",
                "sr" => "sr-RS, sr",
                "st" => "st-ZA, st, en-ZA, en-GB",
                "szl" => {
                    add_en_us = false;
                    "szl, pl-PL, pl, en, de"
                }
                "ta" => "ta-IN, ta",
                "te" => "te-IN, te",
                "tl" => "tl-PH, tl",
                "tr" => "tr-TR, tr",
                "trs" => "trs, es-MX, es",
                "ts" => "ts-ZA, ts, en-ZA, en-GB",
                "uk" => "uk-UA, uk",
                "ur" => "ur-PK, ur",
                "uz" => "uz, ru",
                "ve" => "ve-ZA, ve, en-ZA, en-GB",
                "vi" => "vi-VN, vi",
                "xcl" => "xcl, hy",
                "xh" => "xh-ZA, xh",
                "zam" => "zam, es-MX, es",
                "zh" if langid.region == Some(region!("CN")) => "zh-CN, zh, zh-TW, zh-HK",
                _ => {
                    if langid.region.is_some() {
                        let region = langid.region.unwrap();
                        fmt = format!("{lang}-{region}, {lang}");
                        fmt.as_str()
                    } else {
                        lang.as_str()
                    }
                }
            }
        }
        Err(_) => {
            add_en_us = false;
            "en-US, en"
        }
    };
    if add_en_us {
        out.assign(format!("{langs}, en-US, en").as_str())
    } else {
        out.assign(langs)
    }
}

/// The initial setting of the language drop-down menu
/// in the Fonts and Colors > Advanced preference panel.
///
/// Takes one of the values of the menuitems in the "selectLangs" menulist in
/// https://searchfox.org/firefox-main/source/browser/components/preferences/dialogs/fonts.xhtml
#[no_mangle]
pub extern "C" fn locale_service_default_font_language_group(
    name: &nsACString,
    out: &mut nsACString,
) {
    const X_WESTERN: &'static str = "x-western";
    let font_group = match langid_for_mozilla(name) {
        Ok(mut langid) => match langid.language.as_str() {
            "az" | "mai" | "sat" | "vi" => "x-unicode",
            // These should really have their scripts correctly detected below.
            "bo" => "x-tibt",
            "ckb" | "skr" => "ar",
            "hye" | "xcl" => "x-armn",
            _ => {
                LocaleExpander::new_common().maximize(&mut langid);
                match langid.script.as_ref().map(Script::as_str) {
                    Some("Arab") => "ar",
                    Some("Armn") => "x-armn",
                    Some("Beng") => "x-beng",
                    Some("Cyrl") => "x-cyrillic",
                    Some("Deva") => "x-devanagari",
                    Some("Grek") => "el",
                    Some("Gujr") => "x-gujr",
                    Some("Guru") => "x-guru",
                    Some("Hans") => "zh-CN",
                    Some("Hant") => "zh-TW",
                    Some("Hebr") => "he",
                    Some("Jpan") => "ja",
                    Some("Knda") => "x-knda",
                    Some("Kore") => "ko",
                    Some("Laoo") | Some("Thai") => "th",
                    Some("Mlym") => "x-mlym",
                    Some("Sinh") => "x-sinh",
                    Some("Telu") => "x-telu",
                    Some("Tibt") => "x-tibt",
                    // "Geor" | "Khmr" | "Latn" | "Mymr" | "Taml" => X_WESTERN,
                    _ => X_WESTERN,
                }
            }
        },
        Err(_) => X_WESTERN,
    };
    out.assign(font_group)
}

/// The suffix appended prior to navigating when using `ctrl` or `command`
/// when hitting return/enter in the URL bar.
#[no_mangle]
pub extern "C" fn locale_service_default_url_fixup_suffix(name: &nsACString, out: &mut nsACString) {
    match langid_for_mozilla(name) {
        Ok(langid) => match langid.language.as_str() {
            "be" => out.assign(".by"),
            "cs" => out.assign(".cz"),
            "da" => out.assign(".dk"),
            "nb" | "nn" => out.assign(".no"),
            "sk" => out.assign(".sk"),
            _ => out.assign(".com"),
        },
        Err(_) => out.assign(".com"),
    }
}

#[cfg(test)]
mod tests {
    use super::locale_service_likely_subtags_raw;

    fn transform(input: &[u8], add: bool) -> String {
        let mut out = [0u8; 64];
        let length = unsafe {
            locale_service_likely_subtags_raw(
                input.as_ptr(),
                input.len(),
                add,
                out.as_mut_ptr(),
                out.len(),
            )
        };
        assert!(length >= 0);
        String::from_utf8(out[..length as usize].to_vec()).unwrap()
    }

    #[test]
    fn likely_subtags_matches_cldr_examples() {
        assert_eq!(transform(b"en", true), "en-Latn-US");
        assert_eq!(transform(b"zh", true), "zh-Hans-CN");
        assert_eq!(transform(b"zh-Hant-TW", false), "zh-TW");
    }

    #[test]
    fn likely_subtags_ignores_extensions() {
        assert_eq!(transform(b"en-Latn-US-u-ca-gregory", true), "en-Latn-US");
    }
}
