"""
Profile Loader

This module loads and validates client profiles, global configuration,
and active namespace configuration.
"""

import json
from pathlib import Path
from typing import Optional
from datetime import datetime
from loguru import logger

from .models import ClientProfile, GlobalConfig, ActiveConfig


class ProfileLoader:
    """
    Load and manage enhancement profiles and global configuration
    
    This class provides methods to:
    - Load client-specific profiles
    - Load global configuration
    - Get active namespace
    - Set active namespace (for admin use)
    """
    
    def __init__(self):
        """Initialize profile loader with default paths"""
        self.profiles_dir = Path(__file__).parent / "profiles"
        self.global_config_path = Path(__file__).parent / "global_config.json"
        self.active_config_path = Path(__file__).parent / "active_config.json"
        
        # Caching for performance
        self._profile_cache = {}
        self._global_config = None
        self._active_config = None
        
        logger.debug(f"ProfileLoader initialized with profiles dir: {self.profiles_dir}")
    
    def load_global_config(self, force_reload: bool = False) -> GlobalConfig:
        """
        Load global configuration from global_config.json
        
        Args:
            force_reload: Force reload from disk even if cached
            
        Returns:
            GlobalConfig object with validated configuration
            
        Raises:
            FileNotFoundError: If global_config.json not found
            ValueError: If configuration is invalid
        """
        # Return cached if available and not forcing reload
        if self._global_config and not force_reload:
            return self._global_config
        
        if not self.global_config_path.exists():
            raise FileNotFoundError(
                f"Global config not found at {self.global_config_path}. "
                f"Please create global_config.json in {self.global_config_path.parent}"
            )
        
        try:
            with open(self.global_config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate and parse with Pydantic
            global_config = GlobalConfig(**data)
            
            # Cache it
            self._global_config = global_config
            
            logger.info(f"Global config loaded: {global_config.config_name} v{global_config.config_version}")
            logger.debug(f"  LLM Model: {global_config.llm.model}")
            logger.debug(f"  Embedding Model: {global_config.embedding.model}")
            logger.debug(f"  Pinecone Index: {global_config.vectorstore.pinecone_index}")
            
            return global_config
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in global_config.json: {e}")
            raise ValueError(f"Invalid JSON in global configuration: {e}")
        except Exception as e:
            logger.error(f"Failed to load global config: {e}")
            raise
    
    def load_client_profile(self, client_id: str, force_reload: bool = False) -> ClientProfile:
        """
        Load client profile from profiles/{client_id}.json
        
        Args:
            client_id: Client identifier (e.g., 'client_danamon')
            force_reload: Force reload from disk even if cached
            
        Returns:
            ClientProfile object with validated configuration
            
        Raises:
            FileNotFoundError: If client profile not found
            ValueError: If profile configuration is invalid
        """
        # Return cached if available and not forcing reload
        if client_id in self._profile_cache and not force_reload:
            return self._profile_cache[client_id]
        
        profile_path = self.profiles_dir / f"{client_id}.json"
        
        if not profile_path.exists():
            raise FileNotFoundError(
                f"Client profile '{client_id}' not found at {profile_path}. "
                f"Available profiles: {self.list_available_profiles()}"
            )
        
        try:
            with open(profile_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate and parse with Pydantic
            client_profile = ClientProfile(**data)
            
            # Cache it
            self._profile_cache[client_id] = client_profile
            
            enabled_count = client_profile.get_enabled_count()
            primary_domain = client_profile.get_primary_domain()
            
            logger.info(f"Client profile loaded: {client_profile.client_name} (ID: {client_id})")
            logger.debug(f"  Enabled types: {enabled_count}")
            logger.debug(f"  Primary domain: {primary_domain}")
            logger.debug(f"  Version: {client_profile.version}")
            
            return client_profile
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in profile {client_id}.json: {e}")
            raise ValueError(f"Invalid JSON in client profile '{client_id}': {e}")
        except Exception as e:
            logger.error(f"Failed to load client profile '{client_id}': {e}")
            raise
    
    def get_active_namespace(self, force_reload: bool = False) -> str:
        """
        Get currently active namespace from active_config.json
        
        Args:
            force_reload: Force reload from disk even if cached
            
        Returns:
            Active namespace ID string
            
        Raises:
            FileNotFoundError: If active_config.json not found
            ValueError: If configuration is invalid
        """
        # Return cached if available and not forcing reload
        if self._active_config and not force_reload:
            return self._active_config.active_namespace
        
        if not self.active_config_path.exists():
            raise FileNotFoundError(
                f"Active config not found at {self.active_config_path}. "
                f"Please create active_config.json with 'active_namespace' field."
            )
        
        try:
            with open(self.active_config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate and parse with Pydantic
            active_config = ActiveConfig(**data)
            
            # Cache it
            self._active_config = active_config
            
            logger.info(f"Active namespace: {active_config.active_namespace}")
            if active_config.last_updated:
                logger.debug(f"  Last updated: {active_config.last_updated}")
            
            return active_config.active_namespace
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in active_config.json: {e}")
            raise ValueError(f"Invalid JSON in active configuration: {e}")
        except Exception as e:
            logger.error(f"Failed to load active config: {e}")
            raise
    
    def set_active_namespace(
        self,
        namespace_id: str,
        updated_by: str = "system"
    ) -> None:
        """
        Set active namespace (for admin/system use)
        
        Args:
            namespace_id: Namespace ID to activate
            updated_by: Who updated the config (for audit trail)
            
        Raises:
            ValueError: If namespace is invalid
        """
        # Validate namespace exists in namespaces_config
        try:
            from ..namespaces_config import get_namespace_by_id, validate_namespace
            
            # Check if namespace exists
            ns = get_namespace_by_id(namespace_id)
            if not ns:
                raise ValueError(f"Namespace '{namespace_id}' not found in namespaces_config.py")
            
            # Note: We don't validate is_active anymore since we removed that field
            logger.info(f"Validated namespace: {namespace_id} ({ns.get('name', 'Unknown')})")
            
        except ImportError:
            logger.warning("Could not import namespaces_config for validation")
        
        # Create new active config
        active_config = {
            "active_namespace": namespace_id,
            "last_updated": datetime.now().isoformat(),
            "updated_by": updated_by,
            "_instructions": "Change 'active_namespace' to switch clients. System will load corresponding client profile automatically."
        }
        
        # Write to file
        try:
            with open(self.active_config_path, 'w', encoding='utf-8') as f:
                json.dump(active_config, f, indent=2, ensure_ascii=False)
            
            # Clear cache to force reload next time
            self._active_config = None
            
            logger.info(f"✓ Active namespace changed to: {namespace_id}")
            logger.info(f"  Updated by: {updated_by}")
            logger.info(f"  Timestamp: {active_config['last_updated']}")
            
        except Exception as e:
            logger.error(f"Failed to write active config: {e}")
            raise
    
    def get_profile_for_namespace(self, namespace_id: str) -> ClientProfile:
        """
        Get client profile for a given namespace ID
        
        This is a convenience method that:
        1. Looks up namespace in namespaces_config.py
        2. Gets client_profile field from namespace
        3. Loads the corresponding client profile
        
        Args:
            namespace_id: Namespace ID (e.g., 'danamon-final-3')
            
        Returns:
            ClientProfile object for the namespace
            
        Raises:
            FileNotFoundError: If namespace or profile not found
            ValueError: If namespace has no client_profile defined
        """
        try:
            from ..namespaces_config import get_namespace_by_id
            
            # Get namespace config
            ns_config = get_namespace_by_id(namespace_id)
            
            if not ns_config:
                raise FileNotFoundError(
                    f"Namespace '{namespace_id}' not found in namespaces_config.py"
                )
            
            # Get client_profile field
            client_id = ns_config.get("client_profile")
            
            if not client_id:
                raise ValueError(
                    f"Namespace '{namespace_id}' has no 'client_profile' field defined. "
                    f"Please add 'client_profile' field to this namespace in namespaces_config.py"
                )
            
            logger.debug(f"Namespace '{namespace_id}' → Client profile '{client_id}'")
            
            # Load client profile
            return self.load_client_profile(client_id)
            
        except ImportError as e:
            logger.error(f"Failed to import namespaces_config: {e}")
            raise
    
    def list_available_profiles(self) -> list[str]:
        """
        List all available client profile IDs
        
        Returns:
            List of client profile IDs (without .json extension)
        """
        if not self.profiles_dir.exists():
            return []
        
        profiles = []
        for file in self.profiles_dir.glob("*.json"):
            # Skip template
            if file.stem != "template":
                profiles.append(file.stem)
        
        return sorted(profiles)
    
    def reload_all(self) -> None:
        """
        Force reload all cached configurations
        
        Useful for development or when configs are updated externally
        """
        logger.info("Reloading all configurations...")
        
        self._global_config = None
        self._active_config = None
        self._profile_cache.clear()
        
        # Reload
        self.load_global_config(force_reload=True)
        self.get_active_namespace(force_reload=True)
        
        logger.info("✓ All configurations reloaded")
    
    def get_summary(self) -> dict:
        """
        Get summary of current configuration state
        
        Returns:
            Dictionary with configuration summary
        """
        try:
            global_config = self.load_global_config()
            active_namespace = self.get_active_namespace()
            
            # Try to get active profile
            try:
                active_profile = self.get_profile_for_namespace(active_namespace)
                active_client_name = active_profile.client_name
                active_enabled_count = active_profile.get_enabled_count()
            except Exception:
                active_client_name = "Unknown"
                active_enabled_count = 0
            
            available_profiles = self.list_available_profiles()
            
            return {
                "global_config": {
                    "version": global_config.config_version,
                    "llm_model": global_config.llm.model,
                    "embedding_model": global_config.embedding.model,
                    "pinecone_index": global_config.vectorstore.pinecone_index
                },
                "active_namespace": active_namespace,
                "active_client": active_client_name,
                "active_enabled_types": active_enabled_count,
                "available_profiles": available_profiles,
                "total_profiles": len(available_profiles)
            }
            
        except Exception as e:
            logger.error(f"Failed to get configuration summary: {e}")
            return {"error": str(e)}
