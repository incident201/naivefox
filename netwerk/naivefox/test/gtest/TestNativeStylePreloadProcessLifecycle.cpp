/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "NativeStylePreloadProcessBridge.h"
#include "gtest/gtest.h"

namespace mozilla::naivefox::detail {

TEST(NaiveFoxNativeStylePreloadProcessLifecycle,
     CanceledRouteTombstonesAreGenerationScoped)
{
  NativeStylePreloadProcessCanceledRoutes routes;
  routes.Insert(7, 11);
  routes.Insert(8, 12);

  EXPECT_TRUE(routes.Contains(7, 11));
  EXPECT_FALSE(routes.Contains(7, 12));
  EXPECT_TRUE(routes.Contains(8, 12));

  routes.Remove(7, 12);
  EXPECT_TRUE(routes.Contains(7, 11));
  routes.Remove(7, 11);
  EXPECT_FALSE(routes.Contains(7, 11));
  EXPECT_TRUE(routes.Contains(8, 12));
}

}  // namespace mozilla::naivefox::detail
