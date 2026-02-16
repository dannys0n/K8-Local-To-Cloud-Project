"use client";

import { UserButton as ClerkUserButton } from "@clerk/nextjs";
import { dark } from "@clerk/themes";
import { useTheme } from "next-themes";

type UserButtonProps = {
  imageUrl?: string;
};

export const UserButton = ({ imageUrl }: UserButtonProps) => {
  const { theme } = useTheme();

  return (
    <ClerkUserButton
      imageUrl={imageUrl}
      appearance={{
        baseTheme: theme === "dark" ? dark : undefined,
        elements: {
          avatarBox: "h-[48px] w-[48px]",
        },
      }}
      userProfileMode="navigation"
      userProfileUrl="/account"
    />
  );
};
