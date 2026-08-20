/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_Glean_NaiveFoxMetricTypes_h
#define mozilla_Glean_NaiveFoxMetricTypes_h

#include <stdint.h>

#include "mozilla/Maybe.h"

// NaiveFox has no telemetry product surface.  Necko still refers to generated
// metric constants throughout otherwise useful networking code, so the lean
// build preserves that source ABI with constexpr, allocation-free no-ops.  In
// particular this avoids pulling Glean's DOM WebIDL, JS wrappers and IPC actor
// graph into the networking-only libxul.
enum class DynamicLabel : uint16_t { e__Other__ };

namespace mozilla::glean {

struct NoExtraKeys {};

namespace impl {

enum class CounterType { eBaseOrLabeled, eLabeledOnly };

class NoopTimer final {
 public:
  constexpr NoopTimer() = default;
};

class NoopMetric {
 public:
  constexpr explicit NoopMetric(uint32_t = 0) {}

  template <typename... Args>
  constexpr void Add(Args&&...) const {}
  template <typename... Args>
  constexpr void Set(Args&&...) const {}
  template <typename... Args>
  constexpr void Accumulate(Args&&...) const {}
  template <typename... Args>
  constexpr void AccumulateSingleSample(Args&&...) const {}
  template <typename... Args>
  constexpr void AccumulateRawDuration(Args&&...) const {}
  template <typename... Args>
  constexpr void Record(Args&&...) const {}
  template <typename... Args>
  constexpr void AddToNumerator(Args&&...) const {}

  constexpr NoopTimer Measure() const { return {}; }
  constexpr uint64_t Start() const { return 0; }
  constexpr void StopAndAccumulate(uint64_t) const {}
  constexpr void Cancel(uint64_t) const {}
};

using BooleanMetric = NoopMetric;
using CustomDistributionMetric = NoopMetric;
using DatetimeMetric = NoopMetric;
using DenominatorMetric = NoopMetric;
using DualLabeledCounterMetricBase = NoopMetric;
using MemoryDistributionMetric = NoopMetric;
using NumeratorMetric = NoopMetric;
using ObjectMetric = NoopMetric;
using QuantityMetric = NoopMetric;
using RateMetric = NoopMetric;
using StringListMetric = NoopMetric;
using StringMetric = NoopMetric;
using TextMetric = NoopMetric;
using TimespanMetric = NoopMetric;
using TimingDistributionMetric = NoopMetric;
using UrlMetric = NoopMetric;
using UuidMetric = NoopMetric;

template <CounterType = CounterType::eBaseOrLabeled>
class CounterMetric final : public NoopMetric {
 public:
  using NoopMetric::NoopMetric;
};

class DualLabeledCounterMetric final : public NoopMetric {
 public:
  using NoopMetric::NoopMetric;

  template <typename... Args>
  constexpr CounterMetric<> Get(Args&&...) const {
    return {};
  }
};

template <typename Extra = NoExtraKeys>
class EventMetric final : public NoopMetric {
 public:
  using NoopMetric::NoopMetric;
};

template <typename Metric, typename Label>
class Labeled final {
 public:
  constexpr explicit Labeled(uint32_t = 0) {}

  template <typename... Args>
  constexpr Metric Get(Args&&...) const {
    return Metric(0);
  }

  template <typename AnyLabel>
  constexpr Metric EnumGet(AnyLabel) const {
    return Metric(0);
  }
};

}  // namespace impl
}  // namespace mozilla::glean

#endif  // mozilla_Glean_NaiveFoxMetricTypes_h
