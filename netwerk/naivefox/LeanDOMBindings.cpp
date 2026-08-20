/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

// The full Firefox build emits these plain dictionary operations from the DOM
// WebIDL generator.  NaiveFox does not compile DOM bindings, but OriginAttributes
// remains a core Necko value type.  Keep its non-JS value semantics here.

#include "mozilla/dom/OriginAttributesBinding.h"
#include "mozilla/intl/AppDateTimeFormat.h"
#include "NetworkConnectivityService.h"
#include "DashboardTypes.h"
#include "mozilla/CycleCollectedJSRuntime.h"
#include "mozilla/CycleCollectedJSContext.h"
#include "mozilla/HoldDropJSObjects.h"
#include "ProfileAdditionalInformation.h"
#include "nsNSSCertificateDB.h"
#include "PKCS11ModuleDB.h"

namespace mozilla {
struct SymbolTable;
namespace baseprofiler {
class SpliceableJSONWriter;
}
}

extern "C" bool Gecko_IsInAutomation() { return false; }
extern "C" void JOG_MaybeReload() {}
extern "C" void gfx_critical_note(const char*) {}
nsresult XRE_RunAppShell() { return NS_ERROR_NOT_IMPLEMENTED; }
extern "C" bool profiler_get_symbol_table(const char*, const char*,
                                            mozilla::SymbolTable*) {
  return false;
}
extern "C" bool profiler_demangle_rust(const char*, char*, size_t) {
  return false;
}
extern "C" void gecko_profiler_serialize_marker_for_tag(
    uint8_t, const uint8_t*, uintptr_t,
    mozilla::baseprofiler::SpliceableJSONWriter*) {}
extern "C" void gecko_profiler_stream_marker_schemas(
    mozilla::baseprofiler::SpliceableJSONWriter*, void*) {}

void IPC::ParamTraits<mozilla::ProfileGenerationAdditionalInformation>::Write(
    IPC::MessageWriter*, const paramType&) {}

bool IPC::ParamTraits<mozilla::ProfileGenerationAdditionalInformation>::Read(
    IPC::MessageReader*, paramType*) {
  return false;
}

void IPC::ParamTraits<mozilla::ProfileAndAdditionalInformation>::Write(
    IPC::MessageWriter*, const paramType&) {}

bool IPC::ParamTraits<mozilla::ProfileAndAdditionalInformation>::Read(
    IPC::MessageReader*, paramType*) {
  return false;
}

namespace mozilla::psm {

void CollectThirdPartyPKCS11ModuleTelemetry(bool) {}
void ShowProtectedAuthDialog(const nsCString&, const nsString&) {}

}  // namespace mozilla::psm

NS_IMETHODIMP nsNSSCertificateDB::OpenSignedAppFileAsync(
    AppTrustedRoot, nsIFile*, nsIOpenSignedAppFileCallback*) {
  return NS_ERROR_NOT_IMPLEMENTED;
}

NS_IMETHODIMP nsNSSCertificateDB::AsyncVerifyPKCS7Object(
    const nsTArray<uint8_t>&, const nsTArray<nsTArray<uint8_t>>&,
    nsIX509CertDB::PDFSignatureAlgorithm, JSContext*,
    mozilla::dom::Promise**) {
  return NS_ERROR_NOT_IMPLEMENTED;
}

namespace mozilla::intl {

void AppDateTimeFormat::ClearLocaleCache() {}

}  // namespace mozilla::intl

namespace mozilla {

void InitLateWriteChecks() {}
void BeginLateWriteChecks() {}
void StopLateWriteChecks() {}
void PushSuspendLateWriteChecks() {}
void PopSuspendLateWriteChecks() {}

nsCycleCollectionParticipant* CycleCollectedJSRuntime::GCThingParticipant() {
  return nullptr;
}

nsCycleCollectionParticipant* CycleCollectedJSRuntime::ZoneParticipant() {
  return nullptr;
}

CycleCollectedJSRuntime* CycleCollectedJSRuntime::Get() { return nullptr; }

bool CycleCollectedJSRuntime::AreGCGrayBitsValid() const { return true; }

void CycleCollectedJSRuntime::FixWeakMappingGrayBits() const {}

void CycleCollectedJSRuntime::CheckGrayBits() const {}

void CycleCollectedJSRuntime::GarbageCollect(JS::GCOptions,
                                             JS::GCReason) const {}

void CycleCollectedJSRuntime::FinalizeDeferredThings(DeferredFinalizeType) {}

nsresult CycleCollectedJSRuntime::TraverseRoots(
    nsCycleCollectionNoteRootCallback&) {
  return NS_OK;
}

bool CycleCollectedJSContext::PerformMicroTaskCheckPoint(bool) { return false; }

void CycleCollectedJSContext::ProcessStableStateQueue() {}

void CycleCollectedJSContext::BeginExecutionTracingAsync() {}

void CycleCollectedJSContext::EndExecutionTracingAsync() {}

void CycleCollectedJSRuntime::DumpJSHeap(FILE*) {}

}  // namespace mozilla

void xpc_TryUnmarkWrappedGrayObject(nsISupports*) {}
bool xpc_DumpJSStack(bool, bool, bool) { return false; }

namespace xpc {
void InitializeJSContext() {}
}

namespace mozilla::cyclecollector {

void HoldJSObjectsImpl(void*, nsScriptObjectTracer*, JS::Zone*) {}
void HoldJSObjectsWithKeyImpl(void*, nsScriptObjectTracer*, JSHolderKey*) {}
void HoldJSObjectsImpl(nsISupports*) {}
void HoldJSObjectsWithKeyImpl(nsISupports*, JSHolderKey*) {}
void DropJSObjectsImpl(void*) {}
void DropJSObjectsWithKeyImpl(void*, JSHolderKey*) {}
void DropJSObjectsImpl(nsISupports*) {}
void DropJSObjectsWithKeyImpl(nsISupports*, JSHolderKey*) {}

}  // namespace mozilla::cyclecollector

extern "C" {
void DomPromise_AddRef(const void*) {}
void DomPromise_Release(const void*) {}
void DomPromise_ResolveWithUndefined(const void*) {}
void DomPromise_RejectWithUndefined(const void*) {}
void DomPromise_ResolveWithVariant(const void*, const void*) {}
void DomPromise_RejectWithVariant(const void*, const void*) {}
void DomPromise_RejectWithNsresult(const void*, nsresult) {}
}

namespace mozilla::net {

void HttpConnInfo::SetHTTPProtocolVersion(HttpVersion aVersion) {
  switch (aVersion) {
    case HttpVersion::v0_9:
      protocolVersion.AssignLiteral(u"http/0.9");
      break;
    case HttpVersion::v1_0:
      protocolVersion.AssignLiteral(u"http/1.0");
      break;
    case HttpVersion::v1_1:
      protocolVersion.AssignLiteral(u"http/1.1");
      break;
    case HttpVersion::v2_0:
      protocolVersion.AssignLiteral(u"http/2");
      break;
    case HttpVersion::v3_0:
      protocolVersion.AssignLiteral(u"http/3");
      break;
    default:
      protocolVersion.AssignLiteral(u"unknown protocol version");
  }
}

already_AddRefed<NetworkConnectivityService>
NetworkConnectivityService::GetSingleton() {
  return nullptr;
}

already_AddRefed<AddrInfo> NetworkConnectivityService::MapNAT64IPs(
    AddrInfo* aNewRRSet) {
  return do_AddRef(aNewRRSet);
}

}  // namespace mozilla::net

namespace mozilla::dom {

template <typename T>
static void CopyOptional(T& aDestination, const T& aSource) {
  aDestination.Reset();
  if (aSource.WasPassed()) {
    aDestination.Construct(aSource.Value());
  }
}

OriginAttributesDictionary::OriginAttributesDictionary() = default;

bool OriginAttributesDictionary::Init(JSContext*, JS::Handle<JS::Value>,
                                      const char*, bool) {
  return false;
}

OriginAttributesDictionary& OriginAttributesDictionary::operator=(
    const OriginAttributesDictionary& aOther) {
  mFirstPartyDomain = aOther.mFirstPartyDomain;
  mGeckoViewSessionContextId = aOther.mGeckoViewSessionContextId;
  mPartitionKey = aOther.mPartitionKey;
  mPrivateBrowsingId = aOther.mPrivateBrowsingId;
  mUserContextId = aOther.mUserContextId;
  return *this;
}

bool OriginAttributesDictionary::operator==(
    const OriginAttributesDictionary& aOther) const {
  return mFirstPartyDomain == aOther.mFirstPartyDomain &&
         mGeckoViewSessionContextId == aOther.mGeckoViewSessionContextId &&
         mPartitionKey == aOther.mPartitionKey &&
         mPrivateBrowsingId == aOther.mPrivateBrowsingId &&
         mUserContextId == aOther.mUserContextId;
}

PartitionKeyPatternDictionary::PartitionKeyPatternDictionary() = default;

PartitionKeyPatternDictionary& PartitionKeyPatternDictionary::operator=(
    const PartitionKeyPatternDictionary& aOther) {
  CopyOptional(mBaseDomain, aOther.mBaseDomain);
  CopyOptional(mForeignByAncestorContext, aOther.mForeignByAncestorContext);
  CopyOptional(mPort, aOther.mPort);
  CopyOptional(mScheme, aOther.mScheme);
  return *this;
}

OriginAttributesPatternDictionary::OriginAttributesPatternDictionary() =
    default;

OriginAttributesPatternDictionary& OriginAttributesPatternDictionary::operator=(
    const OriginAttributesPatternDictionary& aOther) {
  CopyOptional(mFirstPartyDomain, aOther.mFirstPartyDomain);
  CopyOptional(mGeckoViewSessionContextId,
               aOther.mGeckoViewSessionContextId);
  CopyOptional(mPartitionKey, aOther.mPartitionKey);
  CopyOptional(mPartitionKeyPattern, aOther.mPartitionKeyPattern);
  CopyOptional(mPrivateBrowsingId, aOther.mPrivateBrowsingId);
  CopyOptional(mUserContextId, aOther.mUserContextId);
  return *this;
}

}  // namespace mozilla::dom
