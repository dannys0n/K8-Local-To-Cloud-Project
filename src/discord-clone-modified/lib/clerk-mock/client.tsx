"use client";

import type { ReactNode } from "react";
import { createContext, useCallback, useContext, useEffect } from "react";
import Image from "next/image";
import Link from "next/link";

const MOCK_USER = {
  id: "mock-user-id",
  firstName: "Mock",
  lastName: "User",
  imageUrl: "/logo.png",
  emailAddresses: [{ emailAddress: "mock@example.com" }],
};

const MockAuthContext = createContext({ user: MOCK_USER });

export function ClerkProvider({ children }: { children: ReactNode }) {
  return (
    <MockAuthContext.Provider value={{ user: MOCK_USER }}>
      {children}
    </MockAuthContext.Provider>
  );
}

export function useUser() {
  const ctx = useContext(MockAuthContext);
  return { user: ctx.user, isLoaded: true };
}

export function SignedIn({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function SignedOut({ children }: { children?: ReactNode }) {
  return null;
}

export function SignOutButton({ children }: { children: ReactNode }) {
  const handleSignOut = useCallback(() => {
    window.location.href = "/sign-in";
  }, []);
  return <span onClick={handleSignOut}>{children}</span>;
}

export function UserButton({
  appearance,
  userProfileUrl,
  imageUrl,
}: {
  appearance?: { baseTheme?: unknown; elements?: Record<string, string> };
  userProfileMode?: string;
  userProfileUrl?: string;
  imageUrl?: string;
}) {
  const avatarBoxClass = appearance?.elements?.avatarBox ?? "h-[48px] w-[48px]";
  const avatarSrc = (imageUrl && imageUrl.trim()) || MOCK_USER.imageUrl;
  return (
    <Link href={userProfileUrl ?? "/account"} className="block">
      <Image
        src={avatarSrc}
        alt="Avatar"
        width={48}
        height={48}
        className={`rounded-full ${avatarBoxClass}`}
      />
    </Link>
  );
}

export function SignIn(_props?: { appearance?: { baseTheme?: unknown } }) {
  useEffect(() => {
    window.location.href = "/";
  }, []);
  return (
    <div className="flex flex-col items-center justify-center p-8">
      <p className="text-zinc-500">Redirecting to app (mock auth)...</p>
    </div>
  );
}

export function SignUp(_props?: { appearance?: { baseTheme?: unknown } }) {
  useEffect(() => {
    window.location.href = "/";
  }, []);
  return (
    <div className="flex flex-col items-center justify-center p-8">
      <p className="text-zinc-500">Redirecting to app (mock auth)...</p>
    </div>
  );
}

export function UserProfile(_props?: { appearance?: { baseTheme?: unknown } }) {
  return (
    <div className="rounded-lg border p-6 dark:border-zinc-700 dark:bg-zinc-800/50">
      <h2 className="text-lg font-semibold dark:text-white">Mock User Profile</h2>
      <div className="mt-4 space-y-2 text-sm text-zinc-600 dark:text-zinc-300">
        <p>
          Name: {MOCK_USER.firstName} {MOCK_USER.lastName}
        </p>
        <p>Email: {MOCK_USER.emailAddresses[0]?.emailAddress}</p>
        <p className="text-xs text-zinc-500">(Clerk mock mode — no real auth)</p>
      </div>
    </div>
  );
}
