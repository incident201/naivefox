/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "LoadContextInfo.h"

#ifndef MOZ_NAIVEFOX
#  include "mozilla/StoragePrincipalHelper.h"
#  include "mozilla/dom/ToJSValue.h"
#  include "nsDocShell.h"
#endif
#include "nsIChannel.h"
#ifndef MOZ_NAIVEFOX
#include "nsILoadContext.h"
#include "nsIWebNavigation.h"
#endif
#include "nsNetUtil.h"

using namespace mozilla::dom;
namespace mozilla {
namespace net {

// LoadContextInfo

NS_IMPL_ISUPPORTS(LoadContextInfo, nsILoadContextInfo)

LoadContextInfo::LoadContextInfo(bool aIsAnonymous,
                                 OriginAttributes aOriginAttributes)
    : mIsAnonymous(aIsAnonymous),
      mOriginAttributes(std::move(aOriginAttributes)) {}

NS_IMETHODIMP LoadContextInfo::GetIsPrivate(bool* aIsPrivate) {
  *aIsPrivate = mOriginAttributes.IsPrivateBrowsing();
  return NS_OK;
}

NS_IMETHODIMP LoadContextInfo::GetIsAnonymous(bool* aIsAnonymous) {
  *aIsAnonymous = mIsAnonymous;
  return NS_OK;
}

OriginAttributes const* LoadContextInfo::OriginAttributesPtr() {
  return &mOriginAttributes;
}

NS_IMETHODIMP LoadContextInfo::GetOriginAttributes(
    JSContext* aCx, JS::MutableHandle<JS::Value> aVal) {
#ifdef MOZ_NAIVEFOX
  return NS_ERROR_NOT_AVAILABLE;
#else
  if (NS_WARN_IF(!ToJSValue(aCx, mOriginAttributes, aVal))) {
    return NS_ERROR_FAILURE;
  }
  return NS_OK;
#endif
}

// LoadContextInfoFactory

NS_IMPL_ISUPPORTS(LoadContextInfoFactory, nsILoadContextInfoFactory)

NS_IMETHODIMP LoadContextInfoFactory::GetDefault(
    nsILoadContextInfo** aDefault) {
  nsCOMPtr<nsILoadContextInfo> info =
      GetLoadContextInfo(false, OriginAttributes());
  info.forget(aDefault);
  return NS_OK;
}

NS_IMETHODIMP LoadContextInfoFactory::GetPrivate(
    nsILoadContextInfo** aPrivate) {
  OriginAttributes attrs;
  attrs.SyncAttributesWithPrivateBrowsing(true);
  nsCOMPtr<nsILoadContextInfo> info = GetLoadContextInfo(false, attrs);
  info.forget(aPrivate);
  return NS_OK;
}

NS_IMETHODIMP LoadContextInfoFactory::GetAnonymous(
    nsILoadContextInfo** aAnonymous) {
  nsCOMPtr<nsILoadContextInfo> info =
      GetLoadContextInfo(true, OriginAttributes());
  info.forget(aAnonymous);
  return NS_OK;
}

NS_IMETHODIMP LoadContextInfoFactory::Custom(
    bool aAnonymous, JS::Handle<JS::Value> aOriginAttributes, JSContext* cx,
    nsILoadContextInfo** _retval) {
#ifdef MOZ_NAIVEFOX
  return NS_ERROR_NOT_AVAILABLE;
#else
  OriginAttributes attrs;
  bool status = attrs.Init(cx, aOriginAttributes);
  NS_ENSURE_TRUE(status, NS_ERROR_FAILURE);

  nsCOMPtr<nsILoadContextInfo> info = GetLoadContextInfo(aAnonymous, attrs);
  info.forget(_retval);
  return NS_OK;
#endif
}

NS_IMETHODIMP LoadContextInfoFactory::FromLoadContext(
    nsILoadContext* aLoadContext, bool aAnonymous,
    nsILoadContextInfo** _retval) {
  nsCOMPtr<nsILoadContextInfo> info =
      GetLoadContextInfo(aLoadContext, aAnonymous);
  info.forget(_retval);
  return NS_OK;
}

NS_IMETHODIMP LoadContextInfoFactory::FromWindow(nsIDOMWindow* aWindow,
                                                 bool aAnonymous,
                                                 nsILoadContextInfo** _retval) {
  nsCOMPtr<nsILoadContextInfo> info = GetLoadContextInfo(aWindow, aAnonymous);
  info.forget(_retval);
  return NS_OK;
}

// Helper functions

already_AddRefed<LoadContextInfo> GetLoadContextInfo(nsIChannel* aChannel) {
  nsresult rv;

  bool pb = NS_UsePrivateBrowsing(aChannel);

  bool anon = false;
  nsLoadFlags loadFlags;
  rv = aChannel->GetLoadFlags(&loadFlags);
  if (NS_SUCCEEDED(rv)) {
    anon = !!(loadFlags & nsIChannel::LOAD_ANONYMOUS);
  }

  OriginAttributes oa;
#ifdef MOZ_NAIVEFOX
  oa.SyncAttributesWithPrivateBrowsing(pb);
#else
  StoragePrincipalHelper::GetOriginAttributesForNetworkState(aChannel, oa);
  MOZ_ASSERT(pb == (oa.IsPrivateBrowsing()));
#endif

  return MakeAndAddRef<LoadContextInfo>(anon, std::move(oa));
}

already_AddRefed<LoadContextInfo> GetLoadContextInfo(
    nsILoadContext* aLoadContext, bool aIsAnonymous) {
#ifdef MOZ_NAIVEFOX
  return MakeAndAddRef<LoadContextInfo>(aIsAnonymous, OriginAttributes());
#else
  if (!aLoadContext) {
    return MakeAndAddRef<LoadContextInfo>(aIsAnonymous, OriginAttributes());
  }

  OriginAttributes oa;
  aLoadContext->GetOriginAttributes(oa);

#ifdef DEBUG
  nsCOMPtr<nsIDocShell> docShell = do_QueryInterface(aLoadContext);
  if (!docShell ||
      nsDocShell::Cast(docShell)->GetBrowsingContext()->IsContent()) {
    MOZ_ASSERT(aLoadContext->UsePrivateBrowsing() == (oa.IsPrivateBrowsing()));
  }
#endif

  return MakeAndAddRef<LoadContextInfo>(aIsAnonymous, std::move(oa));
#endif
}

already_AddRefed<LoadContextInfo> GetLoadContextInfo(nsIDOMWindow* aWindow,
                                                     bool aIsAnonymous) {
#ifdef MOZ_NAIVEFOX
  return MakeAndAddRef<LoadContextInfo>(aIsAnonymous, OriginAttributes());
#else
  nsCOMPtr<nsIWebNavigation> webNav = do_GetInterface(aWindow);
  nsCOMPtr<nsILoadContext> loadContext = do_QueryInterface(webNav);

  return GetLoadContextInfo(loadContext, aIsAnonymous);
#endif
}

already_AddRefed<LoadContextInfo> GetLoadContextInfo(
    nsILoadContextInfo* aInfo) {
  return MakeAndAddRef<LoadContextInfo>(aInfo->IsAnonymous(),
                                        *aInfo->OriginAttributesPtr());
}

already_AddRefed<LoadContextInfo> GetLoadContextInfo(
    bool const aIsAnonymous, OriginAttributes const& aOriginAttributes) {
  return MakeAndAddRef<LoadContextInfo>(aIsAnonymous, aOriginAttributes);
}

}  // namespace net
}  // namespace mozilla
