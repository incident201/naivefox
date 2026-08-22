/*
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "OSPreferences.h"
#include "mozilla/Preferences.h"
#include "mozilla/intl/Locale.h"

#ifndef MOZ_NAIVEFOX
#  include "mozilla/java/GeckoAppShellWrappers.h"
#endif

using namespace mozilla::intl;

OSPreferences::OSPreferences() {}

bool OSPreferences::ReadSystemLocales(nsTArray<nsCString>& aLocaleList) {
#ifdef MOZ_NAIVEFOX
  nsAutoCString locale(Locale::GetDefaultLocale());
  if (CanonicalizeLanguageTag(locale)) {
    aLocaleList.AppendElement(locale);
    return true;
  }
  return false;
#else
  if (!mozilla::jni::IsAvailable()) {
    return false;
  }

  // XXX: Notice, this value may be empty on an early read. In that case
  //     we won't add anything to the return list so that it doesn't get
  //     cached in mSystemLocales.
  auto locales = java::GeckoAppShell::GetDefaultLocales();
  if (locales) {
    for (size_t i = 0; i < locales->Length(); i++) {
      jni::String::LocalRef locale = locales->GetElement(i);
      aLocaleList.AppendElement(locale->ToCString());
    }
    return true;
  }
  return false;
#endif
}

bool OSPreferences::ReadRegionalPrefsLocales(nsTArray<nsCString>& aLocaleList) {
  // For now we're just taking System Locales since we don't know of any better
  // API for regional prefs.
  return ReadSystemLocales(aLocaleList);
}

/*
 * Similar to Gtk, Android does not provide a way to customize or format
 * date/time patterns, so we're reusing ICU data here, but we do modify it
 * according to the Android DateFormat is24HourFormat setting.
 */
bool OSPreferences::ReadDateTimePattern(DateTimeFormatStyle aDateStyle,
                                        DateTimeFormatStyle aTimeStyle,
                                        const nsACString& aLocale,
                                        nsACString& aRetVal) {
#ifndef MOZ_NAIVEFOX
  if (!mozilla::jni::IsAvailable()) {
    return false;
  }
#endif

  nsAutoCString skeleton;
  if (!GetDateTimeSkeletonForStyle(aDateStyle, aTimeStyle, aLocale, skeleton)) {
    return false;
  }

  // Customize the skeleton if necessary to reflect user's 12/24hr pref
#ifndef MOZ_NAIVEFOX
  OverrideSkeletonHourCycle(java::GeckoAppShell::GetIs24HourFormat(), skeleton);
#endif

  if (!GetPatternForSkeleton(skeleton, aLocale, aRetVal)) {
    return false;
  }

  return true;
}

void OSPreferences::RemoveObservers() {}
