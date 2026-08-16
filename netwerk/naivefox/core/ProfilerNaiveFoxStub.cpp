/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "GeckoProfiler.h"
#include "ProfilerControl.h"
#include "mozilla/MozPromise.h"
#include "mozilla/ProfileChunkedBuffer.h"
#include "mozilla/ProfilerMarkers.h"
#include "mozilla/ProfilerThreadRegistry.h"
#include "mozilla/ProfilerThreadRegistration.h"
#include "mozilla/ProfilerBandwidthCounter.h"
#include "mozilla/TimeStamp.h"
#include "mozilla/UniquePtr.h"
#include "nsString.h"
#include "nsThreadUtils.h"

#ifdef XP_WIN
#  include "ETWTools.h"
namespace ETW {
std::atomic<ULONGLONG> gETWCollectionMask = 0;
TRACELOGGING_DEFINE_PROVIDER(kFirefoxTraceLoggingProvider,
                             "Mozilla.FirefoxTraceLogger",
                             (0xc923f508, 0x96e4, 0x5515, 0xe3, 0x2c, 0x75,
                              0x39, 0xd1, 0xb1, 0x05, 0x04));
void Init() {}
void Shutdown() {}
}  // namespace ETW
#endif

// No-op profiler stub implementation for NaiveFox headless runtime

using namespace mozilla;

namespace mozilla::profiler {
Atomic<uint32_t, MemoryOrdering::Relaxed> detail::RacyFeatures::sActiveAndFeatures(0);
MOZ_THREAD_LOCAL(ThreadRegistration*) ThreadRegistration::tlsThreadRegistration;
ThreadRegistry::RegistryMutex ThreadRegistry::sRegistryMutex;
ThreadRegistry::RegistryContainer ThreadRegistry::sRegistryContainer;
}

ProfileChunkedBuffer& profiler_get_core_buffer() {
  static ProfileChunkedBuffer sDummy(ProfileChunkedBuffer::ThreadSafety::WithoutMutex);
  return sDummy;
}

void ProfilerBacktraceDestructor::operator()(ProfilerBacktrace*) {}

ProfilingStack* profiler_register_thread(const char* name, void* guessStackTop) {
  return nullptr;
}

void profiler_unregister_thread() {}

void profiler_register_page(uint64_t aTabID, uint64_t aInnerWindowID,
                            const nsCString& aUrl,
                            uint64_t aEmbedderInnerWindowID,
                            bool aIsPrivateBrowsing) {}

void profiler_unregister_page(uint64_t aRegisteredInnerWindowID) {}

void profiler_init(void* aStackTop) {}

void profiler_shutdown(IsFastShutdown) {}

RefPtr<GenericPromise> profiler_start(
    mozilla::PowerOfTwo32 aCapacity, double aInterval, uint32_t aFeatures,
    const char** aFilters, uint32_t aFilterCount, uint64_t aActiveTabID,
    const mozilla::Maybe<double>& aDuration) {
  return GenericPromise::CreateAndResolve(true, __func__);
}

void profiler_ensure_started(
    mozilla::PowerOfTwo32 aCapacity, double aInterval, uint32_t aFeatures,
    const char** aFilters, uint32_t aFilterCount, uint64_t aActiveTabID,
    const mozilla::Maybe<double>& aDuration) {}

RefPtr<GenericPromise> profiler_stop() {
  return GenericPromise::CreateAndResolve(true, __func__);
}

RefPtr<GenericPromise> profiler_pause() {
  return GenericPromise::CreateAndResolve(true, __func__);
}

RefPtr<GenericPromise> profiler_resume() {
  return GenericPromise::CreateAndResolve(true, __func__);
}

RefPtr<GenericPromise> profiler_pause_sampling() {
  return GenericPromise::CreateAndResolve(true, __func__);
}

RefPtr<GenericPromise> profiler_resume_sampling() {
  return GenericPromise::CreateAndResolve(true, __func__);
}

void profiler_lookup_async_signal_dump_directory() {}

bool profiler_is_paused() { return false; }

bool profiler_feature_active(uint32_t aFeature) { return false; }

bool profiler_active_without_feature(uint32_t aFeature) { return false; }

void profiler_count_bandwidth_bytes(int64_t aBytes) {}

void profiler_request_dump_and_quit_for_test(const nsACString& aFilename) {}

bool profiler_is_locked_on_current_thread() { return false; }

bool profiler_thread_is_being_profiled_for_markers(
    ProfilerThreadId aThreadId) {
  return false;
}

void profiler_thread_sleep() {}

void profiler_thread_wake() {}

ProfilerThreadId profiler_current_thread_id() {
  return ProfilerThreadId{};
}

ProfilerProcessId profiler_current_process_id() {
  return ProfilerProcessId{};
}

UniqueProfilerBacktrace profiler_get_backtrace() {
  return nullptr;
}

void profiler_set_js_context(mozilla::CycleCollectedJSContext* aContext) {}

void profiler_clear_js_context() {}

bool profiler_is_main_thread() { return NS_IsMainThread(); }

ProfilerThreadId profiler_main_thread_id() {
  return ProfilerThreadId{};
}

double profiler_time() { return 0.0; }

bool profiler_is_sampling_paused() { return true; }

void profiler_dump_and_stop() {}

nsCString profiler_find_dump_path() { return nsCString(); }

void profiler_suspend_and_sample_thread(ProfilerThreadId aThreadId,
                                        uint32_t aFeatures,
                                        ProfilerStackCollector& aCollector,
                                        bool aSampleNative) {}

bool profiler_capture_backtrace_into(
    mozilla::ProfileChunkedBuffer& aChunkedBuffer,
    mozilla::StackCaptureOptions aCaptureOptions) {
  return false;
}

mozilla::UniquePtr<mozilla::ProfileChunkedBuffer> profiler_capture_backtrace() {
  return nullptr;
}

mozilla::ProfileBufferControlledChunkManager*
profiler_get_controlled_chunk_manager() {
  return nullptr;
}

template mozilla::ProfileBufferBlockIndex AddMarkerToBuffer(
    mozilla::ProfileChunkedBuffer&, const mozilla::ProfilerString8View&,
    const mozilla::MarkerCategory&, mozilla::MarkerOptions&&,
    mozilla::baseprofiler::markers::NoPayload);
