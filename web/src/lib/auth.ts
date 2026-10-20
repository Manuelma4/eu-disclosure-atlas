/**
 * Browser sign-in with Keycloak: OAuth 2.0 authorization code flow with PKCE (S256) for the public client
 * `atlas-web`. Passkeys are offered by Keycloak's login page. Tokens live in sessionStorage (cleared when the
 * tab closes) and are only sent to the alerts API. Client-side only.
 */
import { useSyncExternalStore } from "react";

import { routing } from "@/i18n/routing";

const ISSUER = process.env.NEXT_PUBLIC_ATLAS_OIDC_ISSUER ?? "http://localhost:8180/realms/atlas";
const CLIENT_ID = process.env.NEXT_PUBLIC_ATLAS_OIDC_CLIENT_ID ?? "atlas-web";
const ENDPOINTS = {
  authorize: `${ISSUER}/protocol/openid-connect/auth`,
  token: `${ISSUER}/protocol/openid-connect/token`,
  logout: `${ISSUER}/protocol/openid-connect/logout`,
};
const SESSION_KEY = "atlas.session";
const PENDING_KEY = "atlas.pkce";
const CHANGE_EVENT = "atlas-auth";

export type Session = {
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt: number; // epoch ms
  email?: string;
  name?: string;
};

type TokenResponse = {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in: number;
};

/** The page's language prefix (/en, /fr…), so sign-in comes back to the same language. */
const localePrefix = () => {
  const segment = window.location.pathname.split("/")[1];
  return `/${(routing.locales as readonly string[]).includes(segment) ? segment : routing.defaultLocale}`;
};
const callbackUrl = () => `${window.location.origin}${localePrefix()}/account/callback`;

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(): string {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

function claims(jwt: string | undefined): Record<string, unknown> {
  try {
    const part = jwt?.split(".")[1] ?? "";
    return JSON.parse(atob(part.replace(/-/g, "+").replace(/_/g, "/")));
  } catch {
    return {};
  }
}

function store(tokens: TokenResponse, previous?: Session): Session {
  const id = claims(tokens.id_token ?? previous?.idToken);
  const session: Session = {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token ?? previous?.refreshToken,
    idToken: tokens.id_token ?? previous?.idToken,
    expiresAt: Date.now() + tokens.expires_in * 1000,
    email: typeof id.email === "string" ? id.email : undefined,
    name: typeof id.name === "string" ? id.name : undefined,
  };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  window.dispatchEvent(new Event(CHANGE_EVENT));
  return session;
}

function clear() {
  sessionStorage.removeItem(SESSION_KEY);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

/** Redirects to Keycloak; comes back to /<locale>/account/callback, then to `returnTo`. */
export async function signIn(returnTo = window.location.pathname): Promise<void> {
  const verifier = randomString();
  const state = randomString();
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  sessionStorage.setItem(PENDING_KEY, JSON.stringify({ verifier, state, returnTo }));
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    redirect_uri: callbackUrl(),
    scope: "openid email profile",
    state,
    code_challenge: base64url(digest),
    code_challenge_method: "S256",
  });
  window.location.href = new URL(`?${params}`, ENDPOINTS.authorize).href; // Keycloak, not a Next.js route
}

/** Completes sign-in on /<locale>/account/callback; returns the path to go back to. */
export async function completeSignIn(search: string): Promise<string> {
  const params = new URLSearchParams(search);
  const pending = JSON.parse(sessionStorage.getItem(PENDING_KEY) ?? "null") as
    | { verifier: string; state: string; returnTo: string }
    | null;
  sessionStorage.removeItem(PENDING_KEY);
  if (params.get("error")) throw new Error(params.get("error_description") ?? params.get("error") ?? "Sign-in failed");
  if (!pending || params.get("state") !== pending.state) throw new Error("Sign-in expired, please try again");
  const response = await fetch(ENDPOINTS.token, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      code: params.get("code") ?? "",
      redirect_uri: callbackUrl(),
      code_verifier: pending.verifier,
    }),
  });
  if (!response.ok) throw new Error(`Token exchange failed (${response.status})`);
  store((await response.json()) as TokenResponse);
  return pending.returnTo.startsWith("/") ? pending.returnTo : "/";
}

export function signOut(): void {
  const session = readSession();
  clear();
  const params = new URLSearchParams({ client_id: CLIENT_ID, post_logout_redirect_uri: `${window.location.origin}${localePrefix()}` });
  if (session?.idToken) params.set("id_token_hint", session.idToken);
  window.location.href = new URL(`?${params}`, ENDPOINTS.logout).href;
}

function readSession(): Session | null {
  try {
    return JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "null") as Session | null;
  } catch {
    return null;
  }
}

/** A valid access token, refreshed when it is about to expire; null when signed out. */
export async function accessToken(): Promise<string | null> {
  const session = readSession();
  if (!session) return null;
  if (session.expiresAt - Date.now() > 30_000) return session.accessToken;
  if (!session.refreshToken) {
    clear();
    return null;
  }
  const response = await fetch(ENDPOINTS.token, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", client_id: CLIENT_ID, refresh_token: session.refreshToken }),
  });
  if (!response.ok) {
    clear();
    return null;
  }
  return store((await response.json()) as TokenResponse, session).accessToken;
}

// React binding: the raw sessionStorage string is the snapshot, so it is stable between renders.
function subscribe(onChange: () => void) {
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** The current session: undefined while rendering on the server, null when signed out. */
export function useSession(): Session | null | undefined {
  const raw = useSyncExternalStore(
    subscribe,
    () => sessionStorage.getItem(SESSION_KEY) ?? "",
    () => undefined,
  );
  if (raw === undefined) return undefined;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}
