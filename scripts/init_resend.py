# Instantiates the ResendEmailLogger at startup so it's registered in the
# callback manager for user-invite and key-creation email hooks (which don't
# go through the normal LLM callback path).
import os  # noqa: F401
import litellm  # noqa: F401
from litellm.proxy.utils import _get_email_logger_class, InternalUsageCache, DualCache  # noqa: F401
from litellm.litellm_core_utils.litellm_logging import _in_memory_loggers  # noqa: F401
from litellm_enterprise.enterprise_callbacks.send_emails.base_email import BaseEmailLogger  # noqa: F401

email_logger_class = _get_email_logger_class()
if email_logger_class is not None and "RESEND_API_KEY" in os.environ:
    internal_usage_cache = InternalUsageCache(dual_cache=DualCache(default_in_memory_ttl=1))
    email_instance = email_logger_class(
        internal_usage_cache=internal_usage_cache.dual_cache,
    )
    # Register in _in_memory_loggers so _get_custom_logger finds it (for LLM callbacks)
    _in_memory_loggers.append(email_instance)
    # Register in litellm.success_callback so get_custom_loggers_for_type finds it
    # (for user-invite / key-creation hooks that scan the callback lists)
    litellm.success_callback.append(email_instance)
    print(f"[email] Instantiated {email_instance.__class__.__name__} at startup.")  # noqa: T201
else:
    print("[email] No email logger class found or RESEND_API_KEY not set.")  # noqa: T201
