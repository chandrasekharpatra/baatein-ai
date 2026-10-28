import json
from datetime import timedelta

from livekit import api

from baatein.domain.ids import new_id
from baatein.domain.models import User
from baatein.ports.auth import CallGrant


def room_name_for(user_id: str) -> str:
    return f"baatein-{user_id.lower()}-{new_id().lower()}"


class LiveKitCallTokens:
    def __init__(
        self, server_url: str, api_key: str, api_secret: str, agent_name: str, ttl: timedelta
    ) -> None:
        self._server_url = server_url
        self._api_key = api_key
        self._api_secret = api_secret
        self._agent_name = agent_name
        self._ttl = ttl

    def issue(self, user: User) -> CallGrant:
        room = room_name_for(user.id)
        dispatch = api.RoomAgentDispatch(
            agent_name=self._agent_name, metadata=json.dumps({"user_id": user.id})
        )
        token = (
            api.AccessToken(self._api_key, self._api_secret)
            .with_identity(user.id)
            .with_name(user.display_name or user.email)
            .with_grants(api.VideoGrants(room_join=True, room=room))
            .with_room_config(api.RoomConfiguration(agents=[dispatch]))
            .with_ttl(self._ttl)
            .to_jwt()
        )
        return CallGrant(server_url=self._server_url, participant_token=token, room_name=room)
