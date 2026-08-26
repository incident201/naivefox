/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef nsHtml5ContentCreatorFunction_h
#define nsHtml5ContentCreatorFunction_h

#ifdef MOZ_NAIVEFOX
#  include <cstddef>
#  include "nsHtml5LeanContentCreators.h"

namespace mozilla::dom {
using HTMLContentCreatorFunction = std::nullptr_t;
using SVGContentCreatorFunction = std::nullptr_t;
}  // namespace mozilla::dom
#else
#  include "nsGenericHTMLElement.h"
#  include "mozilla/dom/SVGElementFactory.h"

#endif

union nsHtml5ContentCreatorFunction {
  mozilla::dom::HTMLContentCreatorFunction html;
  mozilla::dom::SVGContentCreatorFunction svg;
};

#endif  // nsHtml5ContentCreatorFunction_h
