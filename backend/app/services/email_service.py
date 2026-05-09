"""Email service - stubbed for future Resend integration.

This service provides email functionality for user management:
- Welcome emails for new users
- Password reset emails
- Invitation notifications

Currently stubbed - will be integrated with Resend when EMAIL_ENABLED=true.
"""
import logging
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class EmailService:
    """
    Email service for sending transactional emails.

    Currently stubbed - logs email actions when EMAIL_ENABLED=false.
    Will integrate with Resend API when EMAIL_ENABLED=true.
    """

    def __init__(self):
        self.enabled = getattr(settings, 'EMAIL_ENABLED', False)
        self.from_address = getattr(settings, 'EMAIL_FROM_ADDRESS', 'noreply@oniva.ai')
        self.from_name = getattr(settings, 'EMAIL_FROM_NAME', 'Oniva')

    async def send_welcome_email(
        self,
        email: str,
        name: str,
        org_name: str,
        login_url: str = "http://localhost:3000/login"
    ) -> bool:
        """
        Send welcome email to a new user after signup.

        Args:
            email: User's email address
            name: User's display name
            org_name: Organization name
            login_url: URL to login page

        Returns:
            True if email was sent (or logged in stub mode)
        """
        if self.enabled:

            # from resend import Resend
            # client = Resend(api_key=settings.RESEND_API_KEY)
            # client.emails.send(
            #     from_=f"{self.from_name} <{self.from_address}>",
            #     to=[email],
            #     subject=f"Welcome to {org_name}!",
            #     html=self._render_welcome_template(name, org_name, login_url)
            # )
            logger.info(f"Would send welcome email to {email}")
            return True
        else:
            logger.info(
                f"Stub: Welcome email to {email}: "
                f"Welcome to {org_name}, {name}! Login at {login_url}"
            )
            return True

    async def send_password_reset_email(
        self,
        email: str,
        name: str,
        reset_token: str,
        reset_url: str = "http://localhost:3000/reset-password"
    ) -> bool:
        """
        Send password reset email with reset link.

        Args:
            email: User's email address
            name: User's display name
            reset_token: Password reset token
            reset_url: Base URL for password reset page

        Returns:
            True if email was sent (or logged in stub mode)
        """
        full_reset_url = f"{reset_url}?token={reset_token}"

        if self.enabled:

            logger.info(f"Would send password reset email to {email}")
            return True
        else:
            logger.info(
                f"Stub: Password reset email to {email}: "
                f"Hi {name}, reset your password at {full_reset_url}"
            )
            return True

    async def send_invitation_email(
        self,
        email: str,
        org_name: str,
        inviter_name: str,
        role: str,
        signup_url: str = "http://localhost:3000/signup"
    ) -> bool:
        """
        Send invitation email to a user added by admin.

        Args:
            email: User's email address
            org_name: Organization name
            inviter_name: Name of admin who invited the user
            role: Pre-assigned role
            signup_url: URL to signup page

        Returns:
            True if email was sent (or logged in stub mode)
        """
        if self.enabled:

            logger.info(f"Would send invitation email to {email}")
            return True
        else:
            logger.info(
                f"Stub: Invitation email to {email}: "
                f"{inviter_name} has invited you to join {org_name} as {role}. "
                f"Sign up at {signup_url}"
            )
            return True

    async def send_role_change_notification(
        self,
        email: str,
        name: str,
        old_role: str,
        new_role: str,
        org_name: str
    ) -> bool:
        """
        Send notification when user's role is changed.

        Args:
            email: User's email address
            name: User's display name
            old_role: Previous role
            new_role: New role
            org_name: Organization name

        Returns:
            True if email was sent (or logged in stub mode)
        """
        if self.enabled:

            logger.info(f"Would send role change notification to {email}")
            return True
        else:
            logger.info(
                f"Stub: Role change notification to {email}: "
                f"Hi {name}, your role in {org_name} has been changed from {old_role} to {new_role}."
            )
            return True

    async def send_account_deactivated_notification(
        self,
        email: str,
        name: str,
        org_name: str
    ) -> bool:
        """
        Send notification when user's account is deactivated.

        Args:
            email: User's email address
            name: User's display name
            org_name: Organization name

        Returns:
            True if email was sent (or logged in stub mode)
        """
        if self.enabled:

            logger.info(f"Would send account deactivated notification to {email}")
            return True
        else:
            logger.info(
                f"Stub: Account deactivated notification to {email}: "
                f"Hi {name}, your account in {org_name} has been deactivated."
            )
            return True


# Singleton instance
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    """Get the email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
