/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at http://mozilla.org/MPL/2.0/. */

//! The NaiveFox runtime still exposes the small C++ UnicodeProperties API used
//! by Necko and the retained XPCOM helpers.  The browser normally implements
//! that API through ICU4C.  NaiveFox deliberately does not build ICU4C, so the
//! API is backed by this narrow ICU4X bridge instead of a second Unicode data
//! implementation.

use core::ffi::{c_char, c_int};
use core::ptr;

use icu_casemap::{CaseMapper, CaseMapperBorrowed};
use icu_properties::props::{
    BidiClass, BidiMirroringGlyph, BidiPairedBracketType, CanonicalCombiningClass,
    DefaultIgnorableCodePoint, EastAsianWidth, Emoji, EmojiPresentation, GeneralCategory,
    HangulSyllableType, LineBreak, Lowercase, NumericType, Script, VerticalOrientation,
    XidContinue,
};
use icu_properties::script::ScriptWithExtensions;
use icu_properties::{CodePointMapData, CodePointSetData};

const PROP_BIDI_PAIRED_BRACKET_TYPE: u8 = 0;
const PROP_EAST_ASIAN_WIDTH: u8 = 1;
const PROP_HANGUL_SYLLABLE_TYPE: u8 = 2;
const PROP_IDENTIFIER_STATUS: u8 = 3;
const PROP_LINE_BREAK: u8 = 4;
const PROP_NUMERIC_TYPE: u8 = 5;
const PROP_VERTICAL_ORIENTATION: u8 = 6;

const BIN_DEFAULT_IGNORABLE: u8 = 0;
const BIN_EMOJI: u8 = 1;
const BIN_EMOJI_PRESENTATION: u8 = 2;
const BIN_LOWERCASE: u8 = 3;

#[inline]
fn scalar(code_point: u32) -> Option<char> {
    char::from_u32(code_point)
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_bidi_class(code_point: u32) -> u8 {
    scalar(code_point)
        .map(|ch| CodePointMapData::<BidiClass>::new().get(ch).to_icu4c_value())
        .unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_char_mirror(code_point: u32) -> u32 {
    CodePointMapData::<BidiMirroringGlyph>::new()
        .get32(code_point)
        .mirroring_glyph
        .map(|ch| ch as u32)
        .unwrap_or(code_point)
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_char_type(code_point: u32) -> u8 {
    CodePointMapData::<GeneralCategory>::new()
        .get32(code_point)
        as u8
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_is_mirrored(code_point: u32) -> bool {
    CodePointMapData::<BidiMirroringGlyph>::new()
        .get32(code_point)
        .mirrored
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_combining_class(code_point: u32) -> u8 {
    CodePointMapData::<CanonicalCombiningClass>::new()
        .get32(code_point)
        .to_icu4c_value()
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_int_property(code_point: u32, property: u8) -> i32 {
    match property {
        PROP_BIDI_PAIRED_BRACKET_TYPE => match CodePointMapData::<BidiMirroringGlyph>::new()
            .get32(code_point)
            .paired_bracket_type
        {
            BidiPairedBracketType::None => 0,
            BidiPairedBracketType::Open => 1,
            BidiPairedBracketType::Close => 2,
            _ => 0,
        },
        PROP_EAST_ASIAN_WIDTH => CodePointMapData::<EastAsianWidth>::new()
            .get32(code_point)
            .to_icu4c_value() as i32,
        PROP_HANGUL_SYLLABLE_TYPE => CodePointMapData::<HangulSyllableType>::new()
            .get32(code_point)
            .to_icu4c_value() as i32,
        PROP_LINE_BREAK => CodePointMapData::<LineBreak>::new()
            .get32(code_point)
            .to_icu4c_value() as i32,
        PROP_NUMERIC_TYPE => CodePointMapData::<NumericType>::new()
            .get32(code_point)
            .to_icu4c_value() as i32,
        PROP_VERTICAL_ORIENTATION => CodePointMapData::<VerticalOrientation>::new()
            .get32(code_point)
            .to_icu4c_value() as i32,
        // ICU4X does not currently expose UTS #39 Identifier_Status.  The
        // retained NaiveFox consumers only use the value as an allowed versus
        // restricted hint; XID_Continue is the conservative equivalent for
        // those callers and avoids treating arbitrary symbols as identifiers.
        PROP_IDENTIFIER_STATUS => CodePointSetData::new::<XidContinue>().contains32(code_point) as i32,
        _ => 0,
    }
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_numeric_value(code_point: u32) -> i8 {
    let numeric_type = CodePointMapData::<NumericType>::new().get32(code_point);
    if numeric_type != NumericType::Decimal && numeric_type != NumericType::Digit {
        return -1;
    }

    // ICU's numeric value is only consumed by NaiveFox for decimal digits. A
    // decimal digit is always the tenth code point in one of these Unicode
    // decimal ranges. The table is intentionally kept local and data-only;
    // the classification itself comes from ICU4X above.
    const DECIMAL_RANGES: &[(u32, u32)] = &[
        (0x0030, 0x0039), (0x0660, 0x0669), (0x06f0, 0x06f9), (0x07c0, 0x07c9),
        (0x0966, 0x096f), (0x09e6, 0x09ef), (0x0a66, 0x0a6f), (0x0ae6, 0x0aef),
        (0x0b66, 0x0b6f), (0x0be6, 0x0bef), (0x0c66, 0x0c6f), (0x0ce6, 0x0cef),
        (0x0d66, 0x0d6f), (0x0de6, 0x0def), (0x0e50, 0x0e59), (0x0ed0, 0x0ed9),
        (0x0f20, 0x0f29), (0x1040, 0x1049), (0x1090, 0x1099), (0x17e0, 0x17e9),
        (0x1810, 0x1819), (0x1946, 0x194f), (0x19d0, 0x19d9), (0x1a80, 0x1a89),
        (0x1a90, 0x1a99), (0x1b50, 0x1b59), (0x1bb0, 0x1bb9), (0x1c40, 0x1c49),
        (0x1c50, 0x1c59), (0xa620, 0xa629), (0xa8d0, 0xa8d9), (0xa900, 0xa909),
        (0xa9d0, 0xa9d9), (0xa9f0, 0xa9f9), (0xaa50, 0xaa59), (0xabf0, 0xabf9),
        (0xff10, 0xff19), (0x104a0, 0x104a9), (0x10d30, 0x10d39), (0x11066, 0x1106f),
        (0x110f0, 0x110f9), (0x11136, 0x1113f), (0x111d0, 0x111d9), (0x112f0, 0x112f9),
        (0x11450, 0x11459), (0x114d0, 0x114d9), (0x11650, 0x11659), (0x116c0, 0x116c9),
        (0x11730, 0x11739), (0x118e0, 0x118e9), (0x11950, 0x11959), (0x11c50, 0x11c59),
        (0x11d50, 0x11d59), (0x11da0, 0x11da9), (0x11f50, 0x11f59), (0x16a60, 0x16a69),
        (0x16ac0, 0x16ac9), (0x16b50, 0x16b59), (0x1d7ce, 0x1d7ff), (0x1e140, 0x1e149),
        (0x1e2f0, 0x1e2f9), (0x1e4f0, 0x1e4f9), (0x1e950, 0x1e959), (0x1fbf0, 0x1fbf9),
    ];
    for &(start, end) in DECIMAL_RANGES {
        if (start..=end).contains(&code_point) {
            return ((code_point - start) % 10) as i8;
        }
    }
    -1
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_bidi_paired_bracket(code_point: u32) -> u32 {
    CodePointMapData::<BidiMirroringGlyph>::new()
        .get32(code_point)
        .mirroring_glyph
        .map(|ch| ch as u32)
        .unwrap_or(code_point)
}

#[inline]
fn simple_case_map(
    code_point: u32,
    map: impl FnOnce(CaseMapperBorrowed<'static>, char) -> char,
) -> u32 {
    scalar(code_point)
        .map(|ch| map(CaseMapper::new(), ch) as u32)
        .unwrap_or(code_point)
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_to_upper(code_point: u32) -> u32 {
    simple_case_map(code_point, |mapper, ch| mapper.simple_uppercase(ch))
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_to_lower(code_point: u32) -> u32 {
    simple_case_map(code_point, |mapper, ch| mapper.simple_lowercase(ch))
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_to_title(code_point: u32) -> u32 {
    simple_case_map(code_point, |mapper, ch| mapper.simple_titlecase(ch))
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_fold_case(code_point: u32) -> u32 {
    simple_case_map(code_point, |mapper, ch| mapper.simple_fold(ch))
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_is_lowercase(code_point: u32) -> bool {
    CodePointSetData::new::<Lowercase>().contains32(code_point)
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_has_binary_property(
    code_point: u32,
    property: u8,
) -> bool {
    match property {
        BIN_DEFAULT_IGNORABLE => {
            CodePointSetData::new::<DefaultIgnorableCodePoint>().contains32(code_point)
        }
        BIN_EMOJI => CodePointSetData::new::<Emoji>().contains32(code_point),
        BIN_EMOJI_PRESENTATION => {
            CodePointSetData::new::<EmojiPresentation>().contains32(code_point)
        }
        BIN_LOWERCASE => CodePointSetData::new::<Lowercase>().contains32(code_point),
        _ => false,
    }
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_script(code_point: u32) -> i16 {
    ScriptWithExtensions::new()
        .get_script_val32(code_point)
        .to_icu4c_value() as i16
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_has_script(code_point: u32, script: i16) -> bool {
    ScriptWithExtensions::new()
        .has_script32(code_point, Script::from_icu4c_value(script as u16))
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_script_extensions(
    code_point: u32,
    out: *mut i16,
    capacity: usize,
) -> c_int {
    let Some(out) = (if out.is_null() { None } else { Some(out) }) else {
        return -1;
    };
    let mut count = 0usize;
    for script in ScriptWithExtensions::new()
        .get_script_extensions_val32(code_point)
        .iter()
    {
        if count == capacity {
            return -2;
        }
        unsafe { out.add(count).write(script.to_icu4c_value() as i16) };
        count += 1;
    }
    count as c_int
}

#[no_mangle]
pub extern "C" fn mozilla_icu4x_unicode_script_short_name(script: i16) -> *const c_char {
    use icu_properties::PropertyNamesShort;
    use icu_properties::props::Script as IcuScript;

    thread_local! {
        static NAME: core::cell::RefCell<[u8; 5]> = const { core::cell::RefCell::new([0; 5]) };
    }
    let Some(name) = PropertyNamesShort::<IcuScript>::new()
        .get(IcuScript::from_icu4c_value(script as u16))
    else {
        return ptr::null();
    };
    NAME.with(|slot| {
        let mut slot = slot.borrow_mut();
        if name.len() != 4 {
            return ptr::null();
        }
        slot[..4].copy_from_slice(name.as_bytes());
        slot[4] = 0;
        slot.as_ptr() as *const c_char
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn properties_match_icu4c_numeric_conventions() {
        assert_eq!(super::mozilla_icu4x_unicode_char_type('A' as u32), 1);
        assert_eq!(super::mozilla_icu4x_unicode_char_type('a' as u32), 2);
        assert_eq!(super::mozilla_icu4x_unicode_char_mirror('(' as u32), ')' as u32);
        assert_eq!(super::mozilla_icu4x_unicode_numeric_value('7' as u32), 7);
        assert_eq!(super::mozilla_icu4x_unicode_numeric_value(0x1d7d5), 7);
        assert_eq!(super::mozilla_icu4x_unicode_numeric_value('A' as u32), -1);
    }

    #[test]
    fn case_and_script_bridge_is_stable() {
        assert_eq!(super::mozilla_icu4x_unicode_to_upper('a' as u32), 'A' as u32);
        assert_eq!(super::mozilla_icu4x_unicode_to_lower('Z' as u32), 'z' as u32);
        assert_eq!(super::mozilla_icu4x_unicode_fold_case('K' as u32), 'k' as u32);
        assert!(super::mozilla_icu4x_unicode_has_script('A' as u32, 25));
        let name = super::mozilla_icu4x_unicode_script_short_name(25);
        assert!(!name.is_null());
        let name = unsafe { core::ffi::CStr::from_ptr(name) };
        assert_eq!(name.to_str().unwrap(), "Latn");
    }
}
