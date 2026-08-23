/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef intl_components_UnicodeProperties_h_
#define intl_components_UnicodeProperties_h_

#include "mozilla/intl/BidiClass.h"
#include "mozilla/intl/GeneralCategory.h"
#ifdef MOZ_NAIVEFOX
#  include "mozilla/intl/ICUError.h"
#  include "mozilla/intl/icu4x_unicode_glue.h"
#else
#  include "mozilla/intl/ICU4CGlue.h"
#endif
#include "mozilla/intl/UnicodeScriptCodes.h"
#include "mozilla/Vector.h"

#ifndef MOZ_NAIVEFOX
#include "unicode/uchar.h"
#include "unicode/uscript.h"
#endif

extern "C" {

#ifndef MOZ_NAIVEFOX
uint8_t mozilla_canonical_combining_class(uint32_t c);
#endif

}  // extern "C"

namespace mozilla::intl {

#ifdef MOZ_NAIVEFOX
using ICUResult = Result<Ok, ICUError>;
#endif

/**
 * This component is a Mozilla-focused API for working with text properties.
 */
class UnicodeProperties final {
 public:
  /**
   * Return the BidiClass for the character.
   */
  static inline BidiClass GetBidiClass(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return BidiClass(mozilla_icu4x_unicode_bidi_class(aCh));
#else
    return BidiClass(u_charDirection(aCh));
#endif
  }

  /**
   * Maps the specified character to a "mirror-image" character.
   */
  static inline uint32_t CharMirror(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_char_mirror(aCh);
#else
    return u_charMirror(aCh);
#endif
  }

  /**
   * Return the general category value for the code point.
   */
  static inline GeneralCategory CharType(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return GeneralCategory(mozilla_icu4x_unicode_char_type(aCh));
#else
    return GeneralCategory(u_charType(aCh));
#endif
  }

  /**
   * Determine whether the code point has the Bidi_Mirrored property.
   */
  static inline bool IsMirrored(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_is_mirrored(aCh);
#else
    return u_isMirrored(aCh);
#endif
  }

  /**
   * Returns the combining class of the code point as specified in
   * UnicodeData.txt.
   */
  static inline uint8_t GetCombiningClass(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_combining_class(aCh);
#else
    return mozilla_canonical_combining_class(aCh);
#endif
  }

  enum class IntProperty {
    BidiPairedBracketType,
    EastAsianWidth,
    HangulSyllableType,
    IdentifierStatus,
    LineBreak,
    NumericType,
    VerticalOrientation,
  };

  /**
   * Get the property value for an enumerated or integer Unicode property for a
   * code point.
   */
  static inline int32_t GetIntPropertyValue(uint32_t aCh, IntProperty aProp) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_int_property(aCh, static_cast<uint8_t>(aProp));
#else
    UProperty prop;
    switch (aProp) {
      case IntProperty::BidiPairedBracketType:
        prop = UCHAR_BIDI_PAIRED_BRACKET_TYPE;
        break;
      case IntProperty::EastAsianWidth:
        prop = UCHAR_EAST_ASIAN_WIDTH;
        break;
      case IntProperty::HangulSyllableType:
        prop = UCHAR_HANGUL_SYLLABLE_TYPE;
        break;
      case IntProperty::LineBreak:
        prop = UCHAR_LINE_BREAK;
        break;
      case IntProperty::NumericType:
        prop = UCHAR_NUMERIC_TYPE;
        break;
      case IntProperty::VerticalOrientation:
        prop = UCHAR_VERTICAL_ORIENTATION;
        break;
      case IntProperty::IdentifierStatus:
        prop = UCHAR_IDENTIFIER_STATUS;
        break;
    }
    return u_getIntPropertyValue(aCh, prop);
#endif
  }

  /**
   * Get the numeric value for a Unicode code point as defined in the
   * Unicode Character Database if the input is decimal or a digit,
   * otherwise, returns -1.
   */
  static inline int8_t GetNumericValue(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_numeric_value(aCh);
#else
    UNumericType type =
        UNumericType(GetIntPropertyValue(aCh, IntProperty::NumericType));
    return type == U_NT_DECIMAL || type == U_NT_DIGIT
               ? int8_t(u_getNumericValue(aCh))
               : -1;
#endif
  }

  /**
   * Maps the specified character to its paired bracket character.
   */
  static inline uint32_t GetBidiPairedBracket(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_bidi_paired_bracket(aCh);
#else
    return u_getBidiPairedBracket(aCh);
#endif
  }

  /**
   * The given character is mapped to its uppercase equivalent according to
   * UnicodeData.txt; if the character has no uppercase equivalent, the
   * character itself is returned.
   */
  static inline uint32_t ToUpper(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_to_upper(aCh);
#else
    return u_toupper(aCh);
#endif
  }

  /**
   * The given character is mapped to its lowercase equivalent according to
   * UnicodeData.txt; if the character has no lowercase equivalent, the
   * character itself is returned.
   */
  static inline uint32_t ToLower(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_to_lower(aCh);
#else
    return u_tolower(aCh);
#endif
  }

  /**
   * Check if a code point has the Lowercase Unicode property.
   */
  static inline bool IsLowercase(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_is_lowercase(aCh);
#else
    return u_isULowercase(aCh);
#endif
  }

  /**
   * The given character is mapped to its titlecase equivalent according to
   * UnicodeData.txt; if the character has no titlecase equivalent, the
   * character itself is returned.
   */
  static inline uint32_t ToTitle(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_to_title(aCh);
#else
    return u_totitle(aCh);
#endif
  }

  /**
   * The given character is mapped to its case folding equivalent according to
   * UnicodeData.txt and CaseFolding.txt;
   * if the character has no case folding equivalent, the character
   * itself is returned.
   */
  static inline uint32_t FoldCase(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_fold_case(aCh);
#else
    return u_foldCase(aCh, U_FOLD_CASE_DEFAULT);
#endif
  }

  enum class BinaryProperty {
    DefaultIgnorableCodePoint,
    Emoji,
    EmojiPresentation,
  };

  /**
   * Check a binary Unicode property for a code point.
   */
  static inline bool HasBinaryProperty(uint32_t aCh, BinaryProperty aProp) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_has_binary_property(
        aCh, static_cast<uint8_t>(aProp));
#else
    UProperty prop;
    switch (aProp) {
      case BinaryProperty::DefaultIgnorableCodePoint:
        prop = UCHAR_DEFAULT_IGNORABLE_CODE_POINT;
        break;
      case BinaryProperty::Emoji:
        prop = UCHAR_EMOJI;
        break;
      case BinaryProperty::EmojiPresentation:
        prop = UCHAR_EMOJI_PRESENTATION;
        break;
    }
    return u_hasBinaryProperty(aCh, prop);
#endif
  }

#ifdef MOZ_NAIVEFOX
  static constexpr int32_t kEastAsianAmbiguous = 1;
  static constexpr int32_t kEastAsianHalfwidth = 2;
  static constexpr int32_t kEastAsianFullwidth = 3;
  static constexpr int32_t kEastAsianNarrow = 4;
  static constexpr int32_t kEastAsianWide = 5;
  static constexpr int32_t kEastAsianNeutral = 0;
#endif

  /**
   * Check if the width of aCh is full width, half width or wide.
   */
  static inline bool IsEastAsianWidthFHW(uint32_t aCh) {
    switch (GetIntPropertyValue(aCh, IntProperty::EastAsianWidth)) {
#ifdef MOZ_NAIVEFOX
      case kEastAsianFullwidth:
      case kEastAsianHalfwidth:
      case kEastAsianWide:
#else
      case U_EA_FULLWIDTH:
      case U_EA_HALFWIDTH:
      case U_EA_WIDE:
#endif
        return true;
#ifdef MOZ_NAIVEFOX
      case kEastAsianAmbiguous:
      case kEastAsianNarrow:
      case kEastAsianNeutral:
#else
      case U_EA_AMBIGUOUS:
      case U_EA_NARROW:
      case U_EA_NEUTRAL:
#endif
        return false;
    }
    return false;
  }

  /**
   * Check if the width of aCh is full width, half width or wide
   * excluding emoji.
   */
  static inline bool IsEastAsianWidthFHWexcludingEmoji(uint32_t aCh) {
    switch (GetIntPropertyValue(aCh, IntProperty::EastAsianWidth)) {
#ifdef MOZ_NAIVEFOX
      case kEastAsianFullwidth:
      case kEastAsianHalfwidth:
#else
      case U_EA_FULLWIDTH:
      case U_EA_HALFWIDTH:
#endif
        return true;
#ifdef MOZ_NAIVEFOX
      case kEastAsianWide:
#else
      case U_EA_WIDE:
#endif
        return HasBinaryProperty(aCh, BinaryProperty::Emoji) ? false : true;
#ifdef MOZ_NAIVEFOX
      case kEastAsianAmbiguous:
      case kEastAsianNarrow:
      case kEastAsianNeutral:
#else
      case U_EA_AMBIGUOUS:
      case U_EA_NARROW:
      case U_EA_NEUTRAL:
#endif
        return false;
    }
    return false;
  }

  /**
   * Check if the width of aCh is ambiguous, full width, or wide.
   */
  static inline bool IsEastAsianWidthAFW(uint32_t aCh) {
    switch (GetIntPropertyValue(aCh, IntProperty::EastAsianWidth)) {
#ifdef MOZ_NAIVEFOX
      case kEastAsianAmbiguous:
      case kEastAsianFullwidth:
      case kEastAsianWide:
#else
      case U_EA_AMBIGUOUS:
      case U_EA_FULLWIDTH:
      case U_EA_WIDE:
#endif
        return true;
#ifdef MOZ_NAIVEFOX
      case kEastAsianHalfwidth:
      case kEastAsianNarrow:
      case kEastAsianNeutral:
#else
      case U_EA_HALFWIDTH:
      case U_EA_NARROW:
      case U_EA_NEUTRAL:
#endif
        return false;
    }
    return false;
  }

  /**
   * Check if the width of aCh is full width, or wide.
   */
  static inline bool IsEastAsianWidthFW(uint32_t aCh) {
    switch (GetIntPropertyValue(aCh, IntProperty::EastAsianWidth)) {
#ifdef MOZ_NAIVEFOX
      case kEastAsianFullwidth:
      case kEastAsianWide:
#else
      case U_EA_FULLWIDTH:
      case U_EA_WIDE:
#endif
        return true;
#ifdef MOZ_NAIVEFOX
      case kEastAsianAmbiguous:
      case kEastAsianHalfwidth:
      case kEastAsianNarrow:
      case kEastAsianNeutral:
#else
      case U_EA_AMBIGUOUS:
      case U_EA_HALFWIDTH:
      case U_EA_NARROW:
      case U_EA_NEUTRAL:
#endif
        return false;
    }
    return false;
  }

  /**
   * Check if the width of aCh is East Asian Fullwidth (F).
   */
  static inline bool IsEastAsianFullWidth(char32_t aCh) {
    return GetIntPropertyValue(aCh, IntProperty::EastAsianWidth) ==
#ifdef MOZ_NAIVEFOX
           kEastAsianFullwidth;
#else
           U_EA_FULLWIDTH;
#endif
  }

  /**
   * Check if the CharType of aCh is a letter type.
   */
  static inline bool IsLetter(char32_t aCh) {
    switch (CharType(aCh)) {
      case GeneralCategory::Uppercase_Letter:
      case GeneralCategory::Lowercase_Letter:
      case GeneralCategory::Titlecase_Letter:
      case GeneralCategory::Modifier_Letter:
      case GeneralCategory::Other_Letter:
        return true;
      default:
        return false;
    }
  }

  /**
   * Check if the CharType of aCh is a combining mark type.
   */
  static inline bool IsCombiningMark(char32_t aCh) {
    switch (CharType(aCh)) {
      case GeneralCategory::Nonspacing_Mark:
      case GeneralCategory::Spacing_Mark:
      case GeneralCategory::Enclosing_Mark:
        return true;
      default:
        return false;
    }
  }

  /**
   * Check if the CharType of aCh is a punctuation type.
   */
  static inline bool IsPunctuation(uint32_t aCh) {
    switch (CharType(aCh)) {
      case GeneralCategory::Dash_Punctuation:
      case GeneralCategory::Open_Punctuation:
      case GeneralCategory::Close_Punctuation:
      case GeneralCategory::Connector_Punctuation:
      case GeneralCategory::Other_Punctuation:
      case GeneralCategory::Initial_Punctuation:
      case GeneralCategory::Final_Punctuation:
        return true;
      default:
        return false;
    }
  }

  /**
   * Check if the CharType of aCh is math or other symbol.
   */
  static inline bool IsMathOrMusicSymbol(uint32_t aCh) {
    // Keep this function in sync with is_math_symbol in base_chars.py.
    return CharType(aCh) == GeneralCategory::Math_Symbol ||
           CharType(aCh) == GeneralCategory::Other_Symbol;
  }

  static inline Script GetScriptCode(uint32_t aCh) {
#ifdef MOZ_NAIVEFOX
    return Script(mozilla_icu4x_unicode_script(aCh));
#else
    // We can safely ignore the error code here because uscript_getScript
    // returns USCRIPT_INVALID_CODE in the event of an error.
    UErrorCode err = U_ZERO_ERROR;
    return Script(uscript_getScript(aCh, &err));
#endif
  }

  static inline bool HasScript(uint32_t aCh, Script aScript) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_has_script(aCh, static_cast<int16_t>(aScript));
#else
    return uscript_hasScript(aCh, UScriptCode(aScript));
#endif
  }

  static inline const char* GetScriptShortName(Script aScript) {
#ifdef MOZ_NAIVEFOX
    return mozilla_icu4x_unicode_script_short_name(static_cast<int16_t>(aScript));
#else
    return uscript_getShortName(UScriptCode(aScript));
#endif
  }

  static inline int32_t GetMaxNumberOfScripts() {
#ifdef MOZ_NAIVEFOX
    return static_cast<int32_t>(Script::NUM_SCRIPT_CODES) - 1;
#else
    return u_getIntPropertyMaxValue(UCHAR_SCRIPT);
#endif
  }

  // Return true if aChar belongs to a SEAsian script that is written without
  // word spaces, so we need to use the "complex breaker" to find possible word
  // boundaries. (https://en.wikipedia.org/wiki/Scriptio_continua)
  static bool IsScriptioContinua(char16_t aChar) {
    Script sc = GetScriptCode(aChar);
    return sc == Script::THAI || sc == Script::MYANMAR || sc == Script::KHMER ||
           sc == Script::JAVANESE || sc == Script::BALINESE ||
           sc == Script::SUNDANESE || sc == Script::LAO;
  }

  // Return true if aChar belongs to a cursive script for which inter-character
  // justification should be disabled.
  static bool IsCursiveScript(char32_t aChar) {
    Script sc = GetScriptCode(aChar);
    return sc == Script::ARABIC || sc == Script::SYRIAC || sc == Script::NKO ||
           sc == Script::MANDAIC || sc == Script::MONGOLIAN ||
           sc == Script::PHAGS_PA || sc == Script::HANIFI_ROHINGYA;
  }

  // The code point which has the most script extensions is 0x0965, which has 21
  // script extensions, so choose the vector size as 32 to prevent heap
  // allocation.
  static constexpr size_t kMaxScripts = 32;

  using ScriptExtensionVector = Vector<Script, kMaxScripts>;

  /**
   * Get the script extensions for the given code point, and write the script
   * extensions to aExtensions vector. If the code point has script extensions,
   * the script code (Script::COMMON or Script::INHERITED) will be excluded.
   *
   * If the code point doesn't have any script extension, then its script code
   * will be written to aExtensions vector.
   *
   * If the code point is invalid, Script::UNKNOWN will be written to
   * aExtensions vector.
   *
   * Note: aExtensions will be cleared after calling this method regardless of
   * failure.
   *
   * See [1] for the script code of the code point, [2] for the script
   * extensions.
   *
   * https://www.unicode.org/Public/UNIDATA/Scripts.txt
   * https://www.unicode.org/Public/UNIDATA/ScriptExtensions.txt
   */
  static ICUResult GetExtensions(char32_t aCodePoint,
                                 ScriptExtensionVector& aExtensions) {
#ifdef MOZ_NAIVEFOX
    aExtensions.clear();
    int16_t ext[kMaxScripts];
    int32_t len = mozilla_icu4x_unicode_script_extensions(
        static_cast<uint32_t>(aCodePoint), ext, kMaxScripts);
    if (len < 0) {
      if (len == -2) {
        return Err(ICUError::InternalError);
      }
      aExtensions.infallibleAppend(Script::UNKNOWN);
      return Ok();
    }
    if (!aExtensions.reserve(len)) {
      return Err(ICUError::OutOfMemory);
    }
    for (int32_t i = 0; i < len; ++i) {
      aExtensions.infallibleAppend(Script(ext[i]));
    }
    return Ok();
#else
    // Clear the vector first.
    aExtensions.clear();

    // We cannot pass aExtensions to uscript_getScriptExtension as USCriptCode
    // takes 4 bytes, so create a local UScriptCode array to get the extensions.
    UScriptCode ext[kMaxScripts];
    UErrorCode status = U_ZERO_ERROR;
    int32_t len = uscript_getScriptExtensions(static_cast<UChar32>(aCodePoint),
                                              ext, kMaxScripts, &status);
    if (U_FAILURE(status)) {
      // kMaxScripts should be large enough to hold the maximun number of script
      // extensions.
      MOZ_DIAGNOSTIC_ASSERT(status != U_BUFFER_OVERFLOW_ERROR);
      return Err(ToICUError(status));
    }

    if (!aExtensions.reserve(len)) {
      return Err(ICUError::OutOfMemory);
    }

    for (int32_t i = 0; i < len; i++) {
      aExtensions.infallibleAppend(Script(ext[i]));
    }

    return Ok();
#endif
  }
};

}  // namespace mozilla::intl

#endif
