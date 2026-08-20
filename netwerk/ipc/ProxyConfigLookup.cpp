/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "ProxyConfigLookup.h"

#ifndef MOZ_NAIVEFOX
#  include "ProxyConfigLookupChild.h"
#  include "nsContentUtils.h"
#endif
#include "mozilla/Components.h"
#include "nsICancelable.h"
#include "nsIChannel.h"
#include "nsIProtocolProxyService.h"
#include "nsIProtocolProxyService2.h"
#include "nsIPrincipal.h"
#include "nsIScriptSecurityManager.h"
#include "nsNetUtil.h"
#include "nsServiceManagerUtils.h"
#include "nsThreadUtils.h"

namespace mozilla {
namespace net {

// static
nsresult ProxyConfigLookup::Create(
    std::function<void(nsIProxyInfo*, nsresult)>&& aCallback, nsIURI* aURI,
    uint32_t aProxyResolveFlags, nsICancelable** aLookupCancellable) {
  MOZ_ASSERT(NS_IsMainThread());

  RefPtr<ProxyConfigLookup> lookUp =
      new ProxyConfigLookup(std::move(aCallback), aURI, aProxyResolveFlags);
  return lookUp->DoProxyResolve(aLookupCancellable);
}

ProxyConfigLookup::ProxyConfigLookup(
    std::function<void(nsIProxyInfo*, nsresult)>&& aCallback, nsIURI* aURI,
    uint32_t aProxyResolveFlags)
    : mCallback(std::move(aCallback)),
      mURI(aURI),
      mProxyResolveFlags(aProxyResolveFlags) {}

ProxyConfigLookup::~ProxyConfigLookup() = default;

nsresult ProxyConfigLookup::DoProxyResolve(nsICancelable** aLookupCancellable) {
#ifndef MOZ_NAIVEFOX
  if (!XRE_IsParentProcess()) {
    RefPtr<ProxyConfigLookup> self = this;
    bool result = ProxyConfigLookupChild::Create(
        mURI, mProxyResolveFlags,
        [self](nsIProxyInfo* aProxyinfo, nsresult aResult) {
          self->OnProxyAvailable(nullptr, nullptr, aProxyinfo, aResult);
        });
    return result ? NS_OK : NS_ERROR_FAILURE;
  }
#endif

  nsresult rv;
  nsCOMPtr<nsIChannel> channel;
#ifdef MOZ_NAIVEFOX
  nsCOMPtr<nsIScriptSecurityManager> securityManager =
      do_GetService(NS_SCRIPTSECURITYMANAGER_CONTRACTID, &rv);
  NS_ENSURE_SUCCESS(rv, rv);
  nsCOMPtr<nsIPrincipal> principal;
  rv = securityManager->GetSystemPrincipal(getter_AddRefs(principal));
  NS_ENSURE_SUCCESS(rv, rv);
#else
  nsCOMPtr<nsIPrincipal> principal = nsContentUtils::GetSystemPrincipal();
#endif
  rv = NS_NewChannel(getter_AddRefs(channel), mURI, principal,
                     nsILoadInfo::SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL,
                     nsIContentPolicy::TYPE_OTHER);
  if (NS_FAILED(rv)) {
    return rv;
  }

  nsCOMPtr<nsIProtocolProxyService> pps;
  pps = mozilla::components::ProtocolProxy::Service(&rv);
  if (NS_FAILED(rv)) {
    return rv;
  }

  // using the nsIProtocolProxyService2 allows a minor performance
  // optimization, but if an add-on has only provided the original interface
  // then it is ok to use that version.
  nsCOMPtr<nsICancelable> proxyRequest;
  nsCOMPtr<nsIProtocolProxyService2> pps2 = do_QueryInterface(pps);
  if (pps2) {
    rv = pps2->AsyncResolve2(channel, mProxyResolveFlags, this, nullptr,
                             getter_AddRefs(proxyRequest));
  } else {
    rv = pps->AsyncResolve(channel, mProxyResolveFlags, this, nullptr,
                           getter_AddRefs(proxyRequest));
  }

  if (aLookupCancellable) {
    proxyRequest.forget(aLookupCancellable);
  }

  return rv;
}

NS_IMETHODIMP ProxyConfigLookup::OnProxyAvailable(nsICancelable* aRequest,
                                                  nsIChannel* aChannel,
                                                  nsIProxyInfo* aProxyinfo,
                                                  nsresult aResult) {
  mCallback(aProxyinfo, aResult);
  return NS_OK;
}

NS_IMPL_ISUPPORTS(ProxyConfigLookup, nsIProtocolProxyCallback)

}  // namespace net
}  // namespace mozilla
