import type { NextApiRequest } from "next";

import { DEFAULT_MOCK_USER_ID, getMockUserById } from "./users";

export { auth, currentUser, redirectToSignIn } from "./server-utils";

const COOKIE_NAME = "mock_user_id";

/**
 * Mock getAuth for @clerk/nextjs/server - returns mock userId from cookie
 */
export function getAuth(req?: NextApiRequest | { headers: Headers } | unknown): { userId: string } {
  let cookieValue: string | undefined;
  if (req && typeof req === "object" && "cookies" in req && typeof (req as NextApiRequest).cookies === "object") {
    cookieValue = (req as NextApiRequest).cookies?.[COOKIE_NAME];
  }
  if (cookieValue && getMockUserById(cookieValue)) {
    return { userId: cookieValue };
  }
  return { userId: DEFAULT_MOCK_USER_ID };
}
