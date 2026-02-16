import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import {
  DEFAULT_MOCK_USER_ID,
  getMockUserById,
} from "./users";

const COOKIE_NAME = "mock_user_id";

function getMockUserIdFromCookie(): string {
  const cookieStore = cookies();
  const cookie = cookieStore.get(COOKIE_NAME)?.value;
  if (cookie && getMockUserById(cookie)) {
    return cookie;
  }
  return DEFAULT_MOCK_USER_ID;
}

/** Server-only: used by initialProfile, etc. */
export function auth() {
  return { userId: getMockUserIdFromCookie() };
}

/** Server-only: used by initialProfile */
export async function currentUser() {
  const userId = getMockUserIdFromCookie();
  return getMockUserById(userId);
}

/** Server-only: redirect to sign-in */
export function redirectToSignIn() {
  redirect("/sign-in");
}
