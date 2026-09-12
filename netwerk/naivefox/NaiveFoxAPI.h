/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef netwerk_naivefox_NaiveFoxAPI_h
#define netwerk_naivefox_NaiveFoxAPI_h

#if defined(_WIN32)
#  define NAIVEFOX_EXPORT __declspec(dllexport)
#elif defined(__GNUC__)
#  define NAIVEFOX_EXPORT __attribute__((visibility("default")))
#else
#  define NAIVEFOX_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef enum NaiveFoxStatus {
  NAIVEFOX_STATUS_OK = 0,
  NAIVEFOX_STATUS_RUNTIME_ERROR = 1,
  NAIVEFOX_STATUS_INVALID_ARGUMENT = 2,
  NAIVEFOX_STATUS_ALREADY_USED = 3,
} NaiveFoxStatus;

NAIVEFOX_EXPORT int NaiveFoxMain(int aArgc, char* aArgv[]);
NAIVEFOX_EXPORT int NaiveFoxRunEmbedded(const char* aConfigJson,
                                        const char* aProfilePath,
                                        const char* aRuntimePath);
NAIVEFOX_EXPORT void NaiveFoxRequestStop(void);
NAIVEFOX_EXPORT const char* NaiveFoxVersion(void);

#ifdef __cplusplus
}
#endif

#endif
