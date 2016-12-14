package org.grouplens.samantha.server.retriever;

import org.grouplens.samantha.server.common.AbstractComponentConfig;
import org.grouplens.samantha.server.common.RedisService;
import org.grouplens.samantha.server.expander.EntityExpander;
import org.grouplens.samantha.server.expander.ExpanderUtilities;
import org.grouplens.samantha.server.io.RequestContext;
import play.Configuration;
import play.inject.Injector;

import java.util.List;

public class RedisKeyBasedRetrieverConfig extends AbstractComponentConfig implements RetrieverConfig {
    private final List<String> keyFields;
    private final List<String> retrieveFields;
    private final String indexPrefix;
    private final Injector injector;

    private RedisKeyBasedRetrieverConfig(List<String> keyFields, List<String> retrieveFields,
                                         String indexPrefix, Injector injector, Configuration config) {
        super(config);
        this.keyFields = keyFields;
        this.retrieveFields = retrieveFields;
        this.indexPrefix = indexPrefix;
        this.injector = injector;
    }

    public static RetrieverConfig getRetrieverConfig(Configuration retrieverConfig,
                                                     Injector injector) {
        return new RedisKeyBasedRetrieverConfig(retrieverConfig.getStringList("keyFields"),
                retrieverConfig.getStringList("retrieveFields"),
                retrieverConfig.getString("indexPrefix"), injector, retrieverConfig);
    }

    public Retriever getRetriever(RequestContext requestContext) {
        RedisService redisService = injector.instanceOf(RedisService.class);
        List<EntityExpander> expanders = ExpanderUtilities.getEntityExpanders(requestContext, expandersConfig, injector);
        return new RedisKeyBasedRetriever(redisService, retrieveFields, indexPrefix, keyFields, config, expanders);
    }
}
