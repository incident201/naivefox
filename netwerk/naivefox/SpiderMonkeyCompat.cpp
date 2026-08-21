/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include <algorithm>

#include "js/GCAPI.h"
#include "js/HeapAPI.h"
#include "js/ProfilingStack.h"
#include "js/Promise.h"
#include "js/SliceBudget.h"
#include "js/friend/CycleCollector.h"
#include "jsfriendapi.h"
#include "mozilla/IntegerRange.h"
#include "mozilla/MathAlgorithms.h"

#ifdef MOZ_NAIVEFOX

namespace JS {

// clang-format off

#define NAIVEFOX_SUBCATEGORY_ENUM_BEGIN(name, label, color) \
  enum class NaiveFoxProfilingSubcategory_##name : uint32_t {
#define NAIVEFOX_SUBCATEGORY_ENUM_ITEM(category, name, label) name,
#define NAIVEFOX_SUBCATEGORY_ENUM_END \
  };
MOZ_PROFILING_CATEGORY_LIST(NAIVEFOX_SUBCATEGORY_ENUM_BEGIN,
                            NAIVEFOX_SUBCATEGORY_ENUM_ITEM,
                            NAIVEFOX_SUBCATEGORY_ENUM_END)
#undef NAIVEFOX_SUBCATEGORY_ENUM_BEGIN
#undef NAIVEFOX_SUBCATEGORY_ENUM_ITEM
#undef NAIVEFOX_SUBCATEGORY_ENUM_END

#define NAIVEFOX_CATEGORY_INFO_BEGIN(name, label, color)
#define NAIVEFOX_CATEGORY_INFO_ITEM(category, name, label)               \
  {ProfilingCategory::category,                                          \
   uint32_t(NaiveFoxProfilingSubcategory_##category::name), label},
#define NAIVEFOX_CATEGORY_INFO_END
const ProfilingCategoryPairInfo sNaiveFoxProfilingCategoryPairInfo[] = {
  MOZ_PROFILING_CATEGORY_LIST(NAIVEFOX_CATEGORY_INFO_BEGIN,
                              NAIVEFOX_CATEGORY_INFO_ITEM,
                              NAIVEFOX_CATEGORY_INFO_END)
};
#undef NAIVEFOX_CATEGORY_INFO_BEGIN
#undef NAIVEFOX_CATEGORY_INFO_ITEM
#undef NAIVEFOX_CATEGORY_INFO_END

// clang-format on

const ProfilingCategoryPairInfo& GetProfilingCategoryPairInfo(
    ProfilingCategoryPair aCategoryPair) {
  static_assert(sizeof(sNaiveFoxProfilingCategoryPairInfo) /
                    sizeof(sNaiveFoxProfilingCategoryPairInfo[0]) ==
                uint32_t(ProfilingCategoryPair::COUNT));
  uint32_t categoryPairIndex = uint32_t(aCategoryPair);
  MOZ_RELEASE_ASSERT(categoryPairIndex <=
                     uint32_t(ProfilingCategoryPair::LAST));
  return sNaiveFoxProfilingCategoryPairInfo[categoryPairIndex];
}

bool SliceBudget::checkOverBudget() {
  MOZ_ASSERT(counter <= 0);

  if (isWorkBudget()) {
    return true;
  }

  if (interruptRequested && *interruptRequested) {
    interrupted = true;
  }

  if (interrupted) {
    return true;
  }

  if (isTimeBudget() &&
      mozilla::TimeStamp::Now() >= budget.as<TimeBudget>().deadline) {
    return true;
  }

  counter = StepsPerExpensiveCheck;
  return false;
}

#  ifdef MOZ_DIAGNOSTIC_ASSERT_ENABLED
AutoAssertNoGC::AutoAssertNoGC(JSContext*) : cx_(nullptr) {}

AutoAssertNoGC::~AutoAssertNoGC() = default;

void AutoAssertNoGC::reset() { cx_ = nullptr; }
#  endif

void PrepareForIncrementalGC(JSContext*) {}

void FinishIncrementalGC(JSContext*, GCReason) {}

bool IsIncrementalGCInProgress(JSRuntime*) { return false; }

void ShutdownAsyncTasks(JSContext*) {}

void MaybeClearWeakRefTargets(JSRuntime*, ShouldClearWeakRefTargetCallback,
                              void*) {}

Zone* GetTenuredGCThingZone(GCCellPtr) { return nullptr; }

TraceKind GCCellPtr::outOfLineKind() const { return TraceKind::Null; }

TraceKind GCThingTraceKind(void*) { return TraceKind::Null; }

}  // namespace JS

ProfilingStack::~ProfilingStack() {
  MOZ_RELEASE_ASSERT(stackPointer == 0);

  delete[] frames;
}

void ProfilingStack::ensureCapacitySlow() {
  MOZ_ASSERT(stackPointer >= capacity);
  const uint32_t kInitialCapacity = 4096 / sizeof(js::ProfilingStackFrame);

  uint32_t sp = stackPointer;

  uint32_t newCapacity;
  if (!capacity) {
    newCapacity = kInitialCapacity;
  } else {
    size_t memoryGoal =
        mozilla::RoundUpPow2(capacity * 2 * sizeof(js::ProfilingStackFrame));
    newCapacity = memoryGoal / sizeof(js::ProfilingStackFrame);
  }
  newCapacity = std::max(sp + 1, newCapacity);

  auto* newFrames = new js::ProfilingStackFrame[newCapacity];

  for (auto i : mozilla::IntegerRange(capacity)) {
    newFrames[i] = frames[i];
  }

  js::ProfilingStackFrame* oldFrames = frames;
  frames = newFrames;
  capacity = newCapacity;
  delete[] oldFrames;
}

namespace js {

bool IsSystemZone(JS::Zone*) { return false; }

bool ZoneGlobalsAreAllGray(JS::Zone*) { return false; }

void CommitPendingWrapperPreservations(JSContext*) {}

namespace gc::detail {

bool CellIsMarkedGrayIfKnown(const TenuredCell*) { return false; }

}  // namespace gc::detail
}  // namespace js

#endif
