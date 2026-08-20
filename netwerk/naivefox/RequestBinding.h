/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_dom_RequestBinding_h
#define mozilla_dom_RequestBinding_h

#include <stdint.h>

namespace mozilla::dom {

// Parent-only networking code needs the wire values of Request.destination,
// but NaiveFox never exposes the DOM Request interface.  Keep this compact ABI
// definition in the lean build instead of generating the complete binding.
enum class RequestDestination : uint8_t {
  _empty,
  Audio,
  Audioworklet,
  Document,
  Embed,
  Font,
  Frame,
  Iframe,
  Image,
  Json,
  Manifest,
  Object,
  Paintworklet,
  Report,
  Script,
  Sharedworker,
  Style,
  Text,
  Track,
  Video,
  Worker,
  Xslt,
};

enum class FetchPriority : uint8_t { Auto, High, Low };
enum class RequestMode : uint8_t {
  Same_origin,
  No_cors,
  Cors,
  Navigate,
};
enum class ForceMediaDocument : uint8_t { None, Image, Video };

}  // namespace mozilla::dom

#endif  // mozilla_dom_RequestBinding_h
