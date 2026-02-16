import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export {
  ClerkProvider,
  useUser,
  SignedIn,
  SignedOut,
  SignOutButton,
  UserButton,
  SignIn,
  SignUp,
  UserProfile,
} from "./client";

export function authMiddleware(_opts?: { publicRoutes?: string[] }) {
  return function middleware(_req: NextRequest) {
    return NextResponse.next();
  };
}
