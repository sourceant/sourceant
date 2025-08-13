"""
Code Reviewer Plugin for SourceAnt.

Subscribes to pull request events and generates automated code reviews.
"""

import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List

from src.plugins.base_plugin import BasePlugin, PluginMetadata, PluginType
from src.plugins.event_hooks import event_hooks, HookPriority
from src.integrations.github.github import GitHub
from src.integrations.github.github_oauth_client import GitHubOAuth
from src.llms.llm_factory import llm
from src.models.code_review import CodeReview, Verdict
from src.models.pull_request import PullRequest
from src.models.repository import Repository
from src.utils.diff_parser import parse_diff, ParsedDiff
from src.utils.line_mapper import LineMapper
from src.config.settings import REVIEW_DRAFT_PRS, APP_ENV
from src.utils.logger import logger


class CodeReviewerPlugin(BasePlugin):
    """
    Code Reviewer Plugin that subscribes to pull request events.
    
    Generates automated code reviews using LLMs and posts them to GitHub.
    Only processes GitHub App events (not OAuth events which are for activity tracking).
    """
    
    _plugin_name = "code_reviewer"
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the Code Reviewer plugin."""
        super().__init__(config)
    
    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        return PluginMetadata(
            name="code_reviewer",
            version="1.0.0",
            description="Automated code review generation for pull requests",
            author="SourceAnt Team",
            plugin_type=PluginType.INTEGRATION,
            dependencies=[],
            config_schema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable/disable code reviews",
                        "default": True
                    },
                    "review_draft_prs": {
                        "type": "boolean", 
                        "description": "Review draft pull requests",
                        "default": False
                    }
                }
            },
            enabled=True,
            priority=25  # High priority for core functionality
        )
    
    async def _initialize(self) -> None:
        """Initialize the plugin."""
        logger.info("Initializing Code Reviewer plugin")
        
        # Subscribe to pull request events
        event_hooks.subscribe_to_events(
            plugin_name=self.metadata.name,
            callback=self._handle_event,
            event_types=[
                "pull_request.opened",
                "pull_request.synchronize", 
                "pull_request.reopened",
                "pull_request.ready_for_review"
            ]
        )
        
        logger.info("Code Reviewer plugin initialized and subscribed to PR events")
    
    async def _start(self) -> None:
        """Start the plugin."""
        logger.info("Starting Code Reviewer plugin")
        
        # Verify LLM is available
        try:
            llm_instance = llm()
            logger.info(f"Code Reviewer using LLM: {llm_instance.__class__.__name__}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM for code reviews: {e}")
            raise
        
        logger.info("Code Reviewer plugin started successfully")
    
    async def _stop(self) -> None:
        """Stop the plugin."""
        logger.info("Stopping Code Reviewer plugin")
        # No background tasks to stop
        logger.info("Code Reviewer plugin stopped")
    
    async def _cleanup(self) -> None:
        """Cleanup plugin resources."""
        logger.info("Cleaning up Code Reviewer plugin")
        # Event subscriptions will be cleaned up by plugin manager
        logger.info("Code Reviewer plugin cleanup completed")
    
    async def _handle_event(self, event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle pull request events for code review.
        
        Args:
            event_type: Type of event (e.g., "pull_request.opened")
            event_data: Event data from broadcaster
            
        Returns:
            Processing result dictionary
        """
        try:
            # Only process GitHub App events (not OAuth activity tracking)
            auth_type = event_data.get('auth_type', 'github_app')
            if auth_type != 'github_app':
                logger.debug(f"Skipping {event_type} from {auth_type} - Code Reviewer only processes GitHub App events")
                return {'processed': False, 'reason': 'OAuth events not processed for reviews'}
            
            # Check if plugin is enabled
            if not self.get_config('enabled', True):
                return {'processed': False, 'reason': 'Code Reviewer plugin disabled'}
            
            # Extract event data
            repository_event = event_data.get('repository_event')
            user_context = event_data.get('user_context')
            repository_context = event_data.get('repository_context')
            payload = event_data.get('payload', {})
            
            if not repository_event or not repository_context:
                return {'processed': False, 'reason': 'Missing required event data'}
            
            logger.info(
                f"Processing {event_type} for PR #{repository_event.get('number')} "
                f"in {repository_context.get('full_name')}"
            )
            
            # Create model instances
            repository = Repository(
                name=repository_context['name'],
                owner=repository_context['owner']
            )
            
            pull_request_payload = payload.get("pull_request", {})
            head_sha = pull_request_payload.get("head", {}).get("sha")
            base_sha = pull_request_payload.get("base", {}).get("sha")
            
            pull_request = PullRequest(
                number=repository_event.get('number'),
                title=repository_event.get('title'),
                draft=pull_request_payload.get("draft", False),
                merged=pull_request_payload.get("merged", False),
                base_sha=base_sha,
                head_sha=head_sha,
            )
            
            # Check if we should skip this PR
            skip_reason = self._should_skip_review(pull_request)
            if skip_reason:
                logger.info(f"Skipping review: {skip_reason}")
                return {'processed': False, 'reason': skip_reason}
            
            # Generate and post review
            review_result = await self._generate_and_post_review(repository, pull_request)
            
            # Broadcast review completion event
            if review_result.get('status') == 'success':
                await event_hooks.broadcast_event(
                    event_type='sourceant.review_completed',
                    event_data={
                        'repository': repository_context,
                        'pull_request': {
                            'number': pull_request.number,
                            'title': pull_request.title,
                            'base_sha': pull_request.base_sha,
                            'head_sha': pull_request.head_sha,
                            'draft': pull_request.draft
                        },
                        'user_context': user_context,
                        'review_result': review_result,
                        'original_event': event_data
                    },
                    source_plugin=self.metadata.name
                )
            
            return {
                'processed': True,
                'review_result': review_result,
                'pr_number': pull_request.number,
                'repository': repository_context.get('full_name')
            }
            
        except Exception as e:
            logger.error(f"Error processing {event_type} event: {e}", exc_info=True)
            
            # Broadcast error event
            await event_hooks.broadcast_event(
                event_type='sourceant.review_failed',
                event_data={
                    'error': str(e),
                    'event_type': event_type,
                    'event_data': event_data
                },
                source_plugin=self.metadata.name
            )
            
            return {'processed': False, 'error': str(e)}
    
    def _should_skip_review(self, pull_request: PullRequest) -> Optional[str]:
        """
        Check if we should skip reviewing this pull request.
        
        Args:
            pull_request: PullRequest instance
            
        Returns:
            Reason to skip or None if should proceed
        """
        if pull_request.merged:
            return f"Pull request #{pull_request.number} is already merged"
        
        if pull_request.draft and not self.get_config('review_draft_prs', REVIEW_DRAFT_PRS):
            return f"Pull request #{pull_request.number} is a draft"
        
        if not pull_request.number:
            return "Invalid pull request number"
        
        return None
    
    async def _generate_and_post_review(self, repository: Repository, pull_request: PullRequest) -> Dict[str, Any]:
        """
        Generate code review and post it to GitHub.
        
        Args:
            repository: Repository instance
            pull_request: PullRequest instance
            
        Returns:
            Review generation and posting results
        """
        try:
            # Get GitHub client
            github = GitHub()
            
            # Get diff
            raw_diff = github.get_diff(
                owner=repository.owner,
                repo=repository.name,
                pr_number=pull_request.number,
                base_sha=pull_request.base_sha,
                head_sha=pull_request.head_sha,
            )
            
            if not raw_diff:
                return {
                    'status': 'error',
                    'message': 'No diff could be computed',
                    'error_type': 'no_diff'
                }
            
            # Parse diff and create line mapper
            parsed_files = parse_diff(raw_diff)
            line_mapper = LineMapper(parsed_files)
            
            # Calculate token count
            llm_instance = llm()
            total_tokens = sum(llm_instance.count_tokens(pf.diff_text) for pf in parsed_files)
            
            # Add file content tokens if uploads are disabled
            if not llm_instance.uploads_enabled:
                logger.info("File uploads disabled. Calculating tokens from full file content.")
                for pf in parsed_files:
                    content = github.get_file_content(
                        owner=repository.owner,
                        repo=repository.name,
                        file_path=pf.file_path,
                        sha=pull_request.head_sha,
                    )
                    if content:
                        total_tokens += llm_instance.count_tokens(content)
            
            logger.info(f"Total tokens in diff: {total_tokens}")
            
            # Generate review based on token count
            if total_tokens < llm_instance.token_limit:
                logger.info("Diff is small enough for a single-pass review.")
                final_review = await self._generate_single_pass_review(
                    github, repository, pull_request, raw_diff, parsed_files, line_mapper
                )
            else:
                logger.info("Diff is too large. Performing file-by-file review.")
                final_review = await self._generate_file_by_file_review(
                    github, repository, pull_request, parsed_files, line_mapper
                )
            
            # Post review to GitHub
            post_result = github.post_review(
                repository=repository,
                pull_request=pull_request,
                code_review=final_review,
                line_mapper=line_mapper,
            )
            
            logger.info(f"Review generation and posting completed for PR #{pull_request.number}")
            
            return {
                'status': 'success',
                'review_posted': post_result,
                'suggestions_count': len(final_review.code_suggestions) if final_review.code_suggestions else 0,
                'verdict': final_review.verdict.value if final_review.verdict else 'COMMENT',
                'total_tokens': total_tokens
            }
            
        except Exception as e:
            logger.error(f"Error generating/posting review: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'error_type': 'review_generation_failed'
            }
    
    async def _generate_single_pass_review(
        self,
        github: GitHub,
        repository: Repository,
        pull_request: PullRequest,
        raw_diff: str,
        parsed_files: List[ParsedDiff],
        line_mapper: LineMapper
    ) -> CodeReview:
        """Generate review in a single pass for small diffs."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_paths = self._prepare_llm_file_context(
                github, repository, pull_request, parsed_files, temp_dir
            )
            
            full_review = llm().generate_code_review(
                diff=raw_diff,
                parsed_files=parsed_files,
                file_paths=temp_file_paths,
            )
            
            # Map suggestions to valid positions
            all_suggestions = []
            if full_review and full_review.code_suggestions:
                for suggestion in full_review.code_suggestions:
                    mapped_result = line_mapper.validate_and_map_suggestion(
                        suggestion, strict_mode=(APP_ENV == "production")
                    )
                    if mapped_result:
                        suggestion.position = mapped_result[0]
                        all_suggestions.append(suggestion)
            
            return CodeReview(
                summary=full_review.summary,
                verdict=full_review.verdict,
                code_suggestions=all_suggestions,
                scores=full_review.scores,
            )
    
    async def _generate_file_by_file_review(
        self,
        github: GitHub,
        repository: Repository, 
        pull_request: PullRequest,
        parsed_files: List[ParsedDiff],
        line_mapper: LineMapper
    ) -> CodeReview:
        """Generate review file by file for large diffs."""
        all_suggestions = []
        
        for parsed_file in parsed_files:
            logger.info(f"Reviewing file: {parsed_file.file_path}")
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_file_paths = self._prepare_llm_file_context(
                    github, repository, pull_request, [parsed_file], temp_dir
                )
                
                review_for_file = llm().generate_code_review(
                    diff=parsed_file.diff_text,
                    parsed_files=[parsed_file],
                    file_paths=temp_file_paths,
                )
                
                # Map suggestions for this file
                if review_for_file and review_for_file.code_suggestions:
                    for suggestion in review_for_file.code_suggestions:
                        mapped_result = line_mapper.validate_and_map_suggestion(
                            suggestion, strict_mode=(APP_ENV == "production")
                        )
                        if mapped_result:
                            suggestion.position = mapped_result[0]
                            all_suggestions.append(suggestion)
        
        # Generate overall summary
        summary_obj = llm().generate_summary(all_suggestions)
        verdict = Verdict.REQUEST_CHANGES if all_suggestions else Verdict.APPROVE
        
        return CodeReview(
            summary=summary_obj,
            verdict=verdict,
            code_suggestions=all_suggestions,
        )
    
    def _prepare_llm_file_context(
        self,
        github: GitHub,
        repository: Repository,
        pull_request: PullRequest,
        parsed_files: List[ParsedDiff],
        temp_dir: str,
    ) -> List[str]:
        """Prepare file context for the LLM by creating temporary files."""
        temp_file_paths = []
        
        if pull_request.head_sha:
            for pf in parsed_files:
                content = github.get_file_content(
                    owner=repository.owner,
                    repo=repository.name,
                    file_path=pf.file_path,
                    sha=pull_request.head_sha,
                )
                if content:
                    temp_file_path = Path(temp_dir) / pf.file_path
                    temp_file_path.parent.mkdir(parents=True, exist_ok=True)
                    temp_file_path.write_text(content)
                    temp_file_paths.append(str(temp_file_path))
        
        return temp_file_paths