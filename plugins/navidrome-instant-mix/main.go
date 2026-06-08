package main

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/lifecycle"
	"github.com/navidrome/navidrome/plugins/pdk/go/metadata"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
	"github.com/navidrome/navidrome/plugins/pdk/go/sonicsimilarity"
)

const (
	configDiscocsURL        = "discocsUrl"
	configModel             = "model"
	configCount             = "count"
	configTimeoutSeconds    = "timeoutSeconds"
	configMaxPerArtist      = "maxPerArtist"
	configExcludeSameAlbum  = "excludeSameAlbum"
	configDebugPluginEvents = "debugPluginEvents"
)

const (
	defaultDiscocsURL        = "http://127.0.0.1:8711"
	defaultModel             = "discogs_multi"
	defaultCount             = 50
	defaultTimeoutSeconds    = 10
	defaultMaxPerArtist      = 2
	defaultExcludeSameAlbum  = true
	defaultDebugPluginEvents = false
)

var _ metadata.SimilarSongsByTrackProvider = (*discocsPlugin)(nil)
var _ sonicsimilarity.SonicSimilarity = (*discocsPlugin)(nil)

type discocsPlugin struct{}

type discocsSimilarResponse struct {
	Results []discocsSimilarItem `json:"results"`
}

type discocsSimilarItem struct {
	ItemID     string  `json:"item_id"`
	Artist     string  `json:"artist"`
	Title      string  `json:"title"`
	Album      string  `json:"album"`
	Similarity float64 `json:"similarity"`
}

type pluginEvent struct {
	Event      string `json:"event"`
	ItemID     string `json:"item_id,omitempty"`
	Model      string `json:"model,omitempty"`
	Count      int    `json:"count,omitempty"`
	Status     int    `json:"status,omitempty"`
	DiscocsURL string `json:"discocs_url,omitempty"`
	Message    string `json:"message,omitempty"`
}

func init() {
	lifecycle.Register(&discocsPlugin{})
	metadata.Register(&discocsPlugin{})
	sonicsimilarity.Register(&discocsPlugin{})
}

func main() {}

func (p *discocsPlugin) OnInit() error {
	pdk.Log(
		pdk.LogInfo,
		fmt.Sprintf(
			"[discocs] plugin initialized discocsUrl=%s model=%s count=%d",
			getConfigString(configDiscocsURL, defaultDiscocsURL),
			getConfigString(configModel, defaultModel),
			getConfigInt(configCount, defaultCount),
		),
	)
	postPluginEvent(pluginEvent{
		Event:      "init",
		Model:      getConfigString(configModel, defaultModel),
		Count:      getConfigInt(configCount, defaultCount),
		DiscocsURL: getConfigString(configDiscocsURL, defaultDiscocsURL),
		Message:    "plugin initialized",
	})
	return nil
}

func (p *discocsPlugin) GetSimilarSongsByTrack(input metadata.SimilarSongsByTrackRequest) (*metadata.SimilarSongsResponse, error) {
	model := getConfigString(configModel, defaultModel)
	if input.ID == "" {
		pdk.Log(pdk.LogWarn, "[discocs] similar by track called without input ID")
		postPluginEvent(pluginEvent{Event: "similar_missing_id", Model: model, Message: "input ID is empty"})
		return &metadata.SimilarSongsResponse{}, nil
	}
	count := int(input.Count)
	if count <= 0 {
		count = getConfigInt(configCount, defaultCount)
	}
	postPluginEvent(pluginEvent{
		Event:      "similar_called",
		ItemID:     input.ID,
		Model:      model,
		Count:      count,
		DiscocsURL: getConfigString(configDiscocsURL, defaultDiscocsURL),
		Message:    fmt.Sprintf("name=%q artist=%q", input.Name, input.Artist),
	})
	pdk.Log(
		pdk.LogInfo,
		fmt.Sprintf("[discocs] GetSimilarSongsByTrack id=%s name=%q artist=%q count=%d", input.ID, input.Name, input.Artist, count),
	)
	items, err := getDiscocsSimilar(input.ID, count)
	if err != nil {
		pdk.Log(pdk.LogError, fmt.Sprintf("[discocs] similar request failed id=%s error=%v", input.ID, err))
		postPluginEvent(pluginEvent{
			Event:   "similar_failed",
			ItemID:  input.ID,
			Model:   model,
			Count:   count,
			Message: err.Error(),
		})
		return &metadata.SimilarSongsResponse{}, nil
	}
	songs := make([]metadata.SongRef, 0, len(items))
	for _, item := range items {
		if item.ItemID == "" {
			continue
		}
		songs = append(songs, metadata.SongRef{
			ID:     item.ItemID,
			Name:   item.Title,
			Artist: item.Artist,
			Album:  item.Album,
		})
	}
	pdk.Log(pdk.LogInfo, fmt.Sprintf("[discocs] returning %d similar songs id=%s", len(songs), input.ID))
	postPluginEvent(pluginEvent{
		Event:   "similar_returned",
		ItemID:  input.ID,
		Model:   model,
		Count:   len(songs),
		Message: fmt.Sprintf("discocs_results=%d", len(items)),
	})
	return &metadata.SimilarSongsResponse{Songs: songs}, nil
}

func (p *discocsPlugin) GetSonicSimilarTracks(input sonicsimilarity.GetSonicSimilarTracksRequest) (sonicsimilarity.SonicSimilarityResponse, error) {
	model := getConfigString(configModel, defaultModel)
	if input.Song.ID == "" {
		pdk.Log(pdk.LogWarn, "[discocs] sonic similar called without song ID")
		postPluginEvent(pluginEvent{Event: "sonic_missing_id", Model: model, Message: "song ID is empty"})
		return sonicsimilarity.SonicSimilarityResponse{}, nil
	}
	count := int(input.Count)
	if count <= 0 {
		count = getConfigInt(configCount, defaultCount)
	}
	pdk.Log(
		pdk.LogInfo,
		fmt.Sprintf("[discocs] GetSonicSimilarTracks id=%s name=%q artist=%q count=%d", input.Song.ID, input.Song.Name, input.Song.Artist, count),
	)
	postPluginEvent(pluginEvent{
		Event:      "sonic_called",
		ItemID:     input.Song.ID,
		Model:      model,
		Count:      count,
		DiscocsURL: getConfigString(configDiscocsURL, defaultDiscocsURL),
		Message:    fmt.Sprintf("name=%q artist=%q", input.Song.Name, input.Song.Artist),
	})
	items, err := getDiscocsSimilar(input.Song.ID, count)
	if err != nil {
		pdk.Log(pdk.LogError, fmt.Sprintf("[discocs] sonic similar request failed id=%s error=%v", input.Song.ID, err))
		postPluginEvent(pluginEvent{
			Event:   "sonic_failed",
			ItemID:  input.Song.ID,
			Model:   model,
			Count:   count,
			Message: err.Error(),
		})
		return sonicsimilarity.SonicSimilarityResponse{}, nil
	}
	matches := make([]sonicsimilarity.SonicMatch, 0, len(items))
	for _, item := range items {
		if item.ItemID == "" {
			continue
		}
		matches = append(matches, sonicsimilarity.SonicMatch{
			Song: sonicsimilarity.SongRef{
				ID:     item.ItemID,
				Name:   item.Title,
				Artist: item.Artist,
				Album:  item.Album,
			},
			Similarity: clampSimilarity(item.Similarity),
		})
	}
	pdk.Log(pdk.LogInfo, fmt.Sprintf("[discocs] returning %d sonic similar tracks id=%s", len(matches), input.Song.ID))
	postPluginEvent(pluginEvent{
		Event:   "sonic_returned",
		ItemID:  input.Song.ID,
		Model:   model,
		Count:   len(matches),
		Message: fmt.Sprintf("discocs_results=%d", len(items)),
	})
	return sonicsimilarity.SonicSimilarityResponse{Matches: matches}, nil
}

func (p *discocsPlugin) FindSonicPath(input sonicsimilarity.FindSonicPathRequest) (sonicsimilarity.SonicSimilarityResponse, error) {
	pdk.Log(
		pdk.LogInfo,
		fmt.Sprintf("[discocs] FindSonicPath not implemented start=%s end=%s count=%d", input.StartSong.ID, input.EndSong.ID, input.Count),
	)
	return sonicsimilarity.SonicSimilarityResponse{}, nil
}

func getDiscocsSimilar(itemID string, count int) ([]discocsSimilarItem, error) {
	baseURL := strings.TrimRight(getConfigString(configDiscocsURL, defaultDiscocsURL), "/")
	model := getConfigString(configModel, defaultModel)
	maxPerArtist := getConfigInt(configMaxPerArtist, defaultMaxPerArtist)
	excludeSameAlbum := getConfigBool(configExcludeSameAlbum, defaultExcludeSameAlbum)

	params := url.Values{}
	params.Set("item_id", itemID)
	params.Set("count", strconv.Itoa(count))
	params.Set("model", model)
	params.Set("max_per_artist", strconv.Itoa(maxPerArtist))
	params.Set("exclude_same_album", strconv.FormatBool(excludeSameAlbum))

	apiURL := fmt.Sprintf("%s/navidrome/similar?%s", baseURL, params.Encode())
	pdk.Log(pdk.LogInfo, fmt.Sprintf("[discocs] calling API %s", apiURL))
	postPluginEvent(pluginEvent{
		Event:      "api_request",
		ItemID:     itemID,
		Model:      model,
		Count:      count,
		DiscocsURL: baseURL,
		Message:    apiURL,
	})
	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "GET",
		URL:    apiURL,
		Headers: map[string]string{
			"Accept": "application/json",
		},
	})
	if err != nil {
		return nil, fmt.Errorf("discocs HTTP request failed: %w", err)
	}
	status := resp.StatusCode
	bodyBytes := resp.Body
	pdk.Log(pdk.LogInfo, fmt.Sprintf("[discocs] API status=%d", status))
	postPluginEvent(pluginEvent{
		Event:      "api_response",
		ItemID:     itemID,
		Model:      model,
		Count:      count,
		Status:     int(status),
		DiscocsURL: baseURL,
		Message:    fmt.Sprintf("bytes=%d", len(bodyBytes)),
	})
	if status != 200 {
		body := string(bodyBytes)
		if len(body) > 240 {
			body = body[:240]
		}
		return nil, fmt.Errorf("discocs returned status %d: %s", status, body)
	}
	var parsed discocsSimilarResponse
	if err := json.Unmarshal(bodyBytes, &parsed); err != nil {
		return nil, fmt.Errorf("failed to parse discocs response: %w", err)
	}
	return parsed.Results, nil
}

func clampSimilarity(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 1
	}
	return value
}

func postPluginEvent(event pluginEvent) {
	if !getConfigBool(configDebugPluginEvents, defaultDebugPluginEvents) {
		return
	}
	defer func() {
		if recovered := recover(); recovered != nil {
			pdk.Log(pdk.LogWarn, fmt.Sprintf("[discocs] plugin event post recovered event=%s error=%v", event.Event, recovered))
		}
	}()
	baseURL := strings.TrimRight(getConfigString(configDiscocsURL, defaultDiscocsURL), "/")
	if event.DiscocsURL == "" {
		event.DiscocsURL = baseURL
	}
	payload, err := json.Marshal(event)
	if err != nil {
		pdk.Log(pdk.LogWarn, fmt.Sprintf("[discocs] failed to encode plugin event=%s error=%v", event.Event, err))
		return
	}
	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "POST",
		URL:    fmt.Sprintf("%s/navidrome/plugin-event", baseURL),
		Headers: map[string]string{
			"Content-Type": "application/json",
			"Accept":       "application/json",
		},
		Body: payload,
	})
	if err != nil {
		pdk.Log(pdk.LogWarn, fmt.Sprintf("[discocs] plugin event post failed event=%s error=%v", event.Event, err))
		return
	}
	if resp.StatusCode >= 400 {
		pdk.Log(pdk.LogWarn, fmt.Sprintf("[discocs] plugin event post failed event=%s status=%d", event.Event, resp.StatusCode))
	}
}

func getConfigString(key, fallback string) string {
	if value, ok := pdk.GetConfig(key); ok && value != "" {
		return value
	}
	return fallback
}

func getConfigInt(key string, fallback int) int {
	if value, ok := pdk.GetConfig(key); ok && value != "" {
		if parsed, err := strconv.Atoi(value); err == nil && parsed > 0 {
			return parsed
		}
	}
	return fallback
}

func getConfigBool(key string, fallback bool) bool {
	if value, ok := pdk.GetConfig(key); ok && value != "" {
		parsed, err := strconv.ParseBool(value)
		if err == nil {
			return parsed
		}
	}
	return fallback
}
